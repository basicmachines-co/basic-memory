"""Validated MCP results shared by tools and automatic memory."""

from __future__ import annotations

import json
from typing import Literal

from mcp.types import CallToolResult
from pydantic import BaseModel, Field, TypeAdapter
from tau_agent.messages import ImageContent, TextContent
from tau_agent.tools import AgentToolResult
from tau_agent.types import JSONValue

JSON_OBJECT = TypeAdapter(dict[str, JSONValue])


class WriteReceipt(BaseModel):
    file_path: str = Field(min_length=1)
    action: Literal["created", "updated"]
    error: None = None


def confirm_write(result: CallToolResult) -> WriteReceipt:
    tool_result(result)
    # FastMCP wraps a union return under `result`; a plain object return is direct.
    payload = result.structured_content
    if payload is not None and "result" in payload:
        payload = payload["result"]
    return WriteReceipt.model_validate(payload)


def tool_result(result: CallToolResult) -> AgentToolResult:
    """Preserve MCP errors and structured content; render non-native blocks explicitly."""
    content: list[TextContent | ImageContent] = []
    for block in result.content:
        if block.type == "text":
            content.append(TextContent(text=block.text))
        elif block.type == "image":
            content.append(ImageContent(data=block.data, mime_type=block.mime_type))
        else:
            content.append(TextContent(text=f"MCP {block.type}: {block.model_dump_json()}"))
    if result.structured_content is not None:
        content.append(TextContent(text=json.dumps(result.structured_content, ensure_ascii=False)))
    if result.is_error:
        text = "\n".join(block.text for block in content if isinstance(block, TextContent))
        # Tau marks raised tool failures as errors; AgentToolResult has no is_error field.
        raise RuntimeError(f"Basic Memory tool failed: {text}")
    return AgentToolResult(
        content=content,
        details=JSON_OBJECT.validate_python(result.model_dump(mode="json", exclude_none=True)),
    )
