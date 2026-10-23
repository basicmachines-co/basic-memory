"""Tool refusals for the bm_* MCP tools.

A refusal is a call the tool will not carry out as asked: a note that does not
exist, a revision conflict, an argument combination the tool does not support.
Every refusal is a tool error (`isError: true`), because a client that checks
isError treats a returned "Error: ..." string as success. The message helps the
model recover: it names what was wrong and, when there is one, the call to try
instead.
"""

import json
from dataclasses import dataclass
from typing import Literal

from fastmcp.exceptions import ToolError
from httpx import HTTPStatusError

from basic_memory.mcp.tool_params import OutputFormat

type RefusalCode = Literal[
    "NOT_FOUND",
    "AMBIGUOUS",
    "ALREADY_EXISTS",
    "REVISION_CONFLICT",
    "INVALID_ARGUMENTS",
    "OUTSIDE_PROJECT",
    "LOCKED",
    "UNSUPPORTED",
    # Some items of a multi-file change succeeded and some did not.
    "PARTIAL_FAILURE",
    # Not a refusal: the write left and no answer said whether it landed.
    "OUTCOME_UNKNOWN",
    # Not a refusal: the service that would answer did not.
    "UNAVAILABLE",
]


def refusal(
    code: RefusalCode,
    message: str,
    *,
    try_instead: str | None = None,
    output_format: OutputFormat = "text",
) -> ToolError:
    """Build the error a tool raises to refuse a call.

    Returns the exception rather than raising it, so the call site reads
    `raise refusal(...)` and the type checker sees the branch end.
    """
    if output_format == "json":
        payload: dict[str, str] = {"error": code, "message": message}
        if try_instead is not None:
            payload["try_instead"] = try_instead
        return ToolError(json.dumps(payload))
    if try_instead is None:
        return ToolError(f"{code}: {message}")
    return ToolError(f"{code}: {message}\nTry instead: {try_instead}")


# --- API refusals ---
# The typed clients raise ToolError from the HTTPStatusError when the API answered,
# and from the transport error when it did not. A 4xx answer means the API refused
# and nothing was written; anything else leaves the outcome unknown.

_STATUS_REFUSALS: dict[int, RefusalCode] = {
    400: "INVALID_ARGUMENTS",
    404: "NOT_FOUND",
    409: "REVISION_CONFLICT",
    415: "UNSUPPORTED",
    422: "INVALID_ARGUMENTS",
    423: "LOCKED",
}


@dataclass(frozen=True, slots=True)
class ApiRefusal:
    """A write the API refused: nothing changed."""

    code: RefusalCode
    message: str
    # True for a failed expected_checksum; current_checksum is then None when the
    # note is gone.
    revision_conflict: bool
    current_checksum: str | None

    def with_outcome(self, outcome: str) -> str:
        """The server's message followed by what the tool did, as two sentences."""
        message = self.message.rstrip()
        stop = "" if message.endswith((".", "!", "?")) else "."
        return f"{message}{stop} {outcome}"


def api_refusal(error: ToolError) -> ApiRefusal | None:
    """Read a typed-client failure as an API refusal, or None when the outcome is unknown."""
    cause = error.__cause__
    if not isinstance(cause, HTTPStatusError):
        return None
    code = _STATUS_REFUSALS.get(cause.response.status_code)
    if code is None:
        return None
    # FastAPI answers {"detail": str | {"message": ..., ...}}; a gateway may answer HTML.
    response = cause.response
    is_json = "application/json" in response.headers.get("content-type", "")
    data = response.json() if is_json else None
    detail = data.get("detail") if isinstance(data, dict) else None
    match detail:
        case str() as text:
            message = text
        case {"message": str() as text}:
            message = text
        case _:
            message = str(error)
    return ApiRefusal(
        code=code,
        message=message,
        revision_conflict=isinstance(detail, dict) and "db_checksum" in detail,
        current_checksum=detail.get("db_checksum") if isinstance(detail, dict) else None,
    )


# --- Failed note changes ---
# bm_write, bm_edit, bm_move and bm_delete fail the same few ways, so one place
# words them. Every path given here is already qualified the way the call
# addressed its project, so each suggested call reaches the same note.


def revision_conflict(
    address: str,
    current_checksum: str | None,
    *,
    unchanged: str,
    output_format: OutputFormat,
    if_deleted: str = "bm_search to find where its content went",
) -> ToolError:
    """A failed expected_checksum: the note changed since it was read, or it is gone.

    `unchanged` says what did not happen ("Nothing was moved."); `if_deleted` is
    the way forward when the note no longer exists.
    """
    if current_checksum is None:
        return refusal(
            "REVISION_CONFLICT",
            f"'{address}' was deleted since you read it. {unchanged}",
            try_instead=if_deleted,
            output_format=output_format,
        )
    return refusal(
        "REVISION_CONFLICT",
        f"'{address}' changed since you read it (current checksum {current_checksum}). {unchanged}",
        try_instead=(
            f"bm_read(identifier='{address}'), then retry with "
            f"expected_checksum='{current_checksum}'"
        ),
        output_format=output_format,
    )


def note_change_failure(
    error: ToolError,
    *,
    action: str,
    address: str,
    unchanged: str,
    output_format: OutputFormat,
    occupied: str | None = None,
    subject: Literal["note", "directory"] = "note",
) -> ToolError:
    """Explain a note change the API refused, or one whose outcome is unknown.

    `action` names the change ("edit", "move"); `occupied` is the destination of a
    move, where a plain 409 means a file is already there. `subject` says whether
    the addresses are notes or directories, which decides how to look at them.
    """

    def look_at(path: str) -> str:
        match subject:
            case "note":
                return f"bm_read(identifier='{path}')"
            case "directory":
                return f"bm_list(path='{path}')"

    refused = api_refusal(error)
    if refused is None:
        # Trigger: the request left and no 4xx came back (no response, or a 5xx).
        # Why: the server may have applied it; retrying an append would apply it twice.
        # Outcome: an error that says to read the note before retrying.
        return refusal(
            "OUTCOME_UNKNOWN",
            f"The {action} of '{address}' may or may not have happened: {error}",
            try_instead=f"{look_at(occupied or address)} before retrying",
            output_format=output_format,
        )
    if refused.revision_conflict:
        return revision_conflict(
            address, refused.current_checksum, unchanged=unchanged, output_format=output_format
        )
    if occupied is not None and refused.code == "REVISION_CONFLICT":
        return refusal(
            "ALREADY_EXISTS",
            refused.with_outcome(unchanged),
            try_instead=f"{look_at(occupied)} to see what is there",
            output_format=output_format,
        )
    return refusal(
        refused.code,
        refused.with_outcome(unchanged),
        try_instead=f"{look_at(address)} to see it as it is",
        output_format=output_format,
    )
