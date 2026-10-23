"""Tool refusals for the bm_* MCP tools (SPEC-35.2 C11, #1813).

A refusal is a call the tool will not carry out as asked: a note that does not
exist, a revision conflict, an argument combination the tool does not support.
Every refusal is a tool error (`isError: true`), because a client that checks
isError treats a returned "Error: ..." string as success. The message still has to
help the model recover, so it names what was wrong and, when there is one, the
call to try instead.
"""

import json
from typing import Literal

from fastmcp.exceptions import ToolError

from basic_memory.mcp.tool_params import OutputFormat

type RefusalCode = Literal[
    "NOT_FOUND",
    "AMBIGUOUS",
    "ALREADY_EXISTS",
    "REVISION_CONFLICT",
    "INVALID_ARGUMENTS",
    "OUTSIDE_PROJECT",
    "UNSUPPORTED",
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
