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
