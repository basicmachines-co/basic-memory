"""File reading tool for Basic Memory MCP server.

This module provides tools for reading raw file content directly,
supporting various file types including text, images, and other binary files.
Files are read directly without any knowledge graph processing.
"""

import base64

from typing import Any, Annotated, Optional

from loguru import logger
from fastmcp import Context
from pydantic import AliasChoices, Field
from fastmcp.exceptions import ToolError

from basic_memory.config import ConfigManager
from basic_memory.mcp.project_context import (
    detect_project_from_memory_url_prefix,
    get_project_client,
    resolve_project_and_path,
)
from basic_memory.mcp.async_client import local_project_path
from basic_memory.mcp.file_content import (
    DocumentFile,
    DocumentTooLarge,
    ImageFile,
    TextFile,
    UnsupportedImage,
    classify_file_response,
)
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get, resolve_entity_id
from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import validate_project_path


@mcp.tool(
    title="Read Content",
    description=(
        "Read a file's raw bytes by path, permalink, or memory:// URL, without "
        'knowledge-graph processing. Text and JSON come back as {"type": "text"}; '
        "images are re-encoded as JPEG and downscaled to about 350 KB; other binaries come "
        'back base64 as {"type": "document"} and are refused above 350 KB. Use '
        "bm_read for markdown notes when you want note resolution and fallback search."
    ),
    tags={"notes"},
    annotations={
        "title": "Read Content",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def read_content(
    path: Annotated[
        str,
        Field(validation_alias=AliasChoices("path", "file_path", "filepath", "file")),
    ],
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """Read a file's raw content by path or permalink.

    This tool provides direct access to file content in the knowledge base,
    handling different file types appropriately. Uses stateless architecture -
    project parameter optional with server resolution.

    Supported file types:
    - Text files (markdown, code, etc.) are returned as plain text
    - Images are automatically resized/optimized for display
    - Other binary files are returned as base64 if below size limits

    Args:
        path: The path or permalink to the file. Can be:
            - A regular file path (docs/example.md)
            - A memory URL (memory://docs/example)
            - A permalink (docs/example)
        project: Project name to read from. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from list_memory_projects().
        context: Optional FastMCP context for performance caching.

    Returns:
        A dictionary with the file content and metadata:
        - For text: {"type": "text", "text": "content", "content_type": "text/markdown", "encoding": "utf-8"}
        - For images: {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "base64_data"}}
        - For other files: {"type": "document", "source": {"type": "base64", "media_type": "content_type", "data": "base64_data"}}
        - For errors: {"type": "error", "error": "error message"}

    Examples:
        # Read a markdown file
        result = await read_content("docs/project-specs.md")

        # Read an image
        image_data = await read_content("assets/diagram.png")

        # Read using memory URL
        content = await read_content("memory://docs/architecture")

        # Read configuration file
        config = await read_content("config/settings.json")

        # Explicit project specification
        result = await read_content("docs/project-specs.md", project="my-project")

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        SecurityError: If path attempts path traversal
    """
    # Detect project from memory URL prefix before routing.
    # project_id routes by external UUID, so it bypasses URL discovery entirely.
    if project is None and project_id is None:
        detected = await detect_project_from_memory_url_prefix(
            path,
            ConfigManager().config,
            context=context,
        )
        if detected is not None:
            # The id rides along so the name is never re-resolved against a
            # different accessible workspace holding the same permalink (#1432).
            project, project_id = detected.project, detected.project_id

    logger.debug(f"MCP tool call tool=read_content project={project} path={path}")

    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Resolve path with project-prefix awareness for memory:// URLs.
        # Use active_project.name so resolution stays consistent when project_id
        # was used or `project` was wrong/ambiguous (matches the cached resolution).
        _, url, _ = await resolve_project_and_path(client, path, active_project.name, context)

        # Validate path to prevent path traversal attacks
        # For memory:// URLs, validate the extracted path (not the raw URL which
        # has a scheme prefix that confuses path validation)
        raw_path = memory_url_path(path) if path.startswith("memory://") else path
        project_path = local_project_path(client, active_project)
        if not validate_project_path(raw_path, project_path) or not validate_project_path(
            url, project_path
        ):
            logger.warning(
                "Attempted path traversal attack blocked",
                path=path,
                url=url,
                project=active_project.name,
            )
            return {
                "type": "error",
                "error": f"Path '{path}' is not allowed - paths must stay within project boundaries",
            }

        # Resolve path to entity ID
        try:
            entity_id = await resolve_entity_id(client, active_project.external_id, url)
        except ToolError:
            # Convert resolution errors to "Resource not found" for consistency
            raise ToolError(f"Resource not found: {url}")

        # Call the v2 resource endpoint
        response = await call_get(
            client, f"/v2/projects/{active_project.external_id}/resource/{entity_id}"
        )
        file_content = classify_file_response(response)
        logger.debug(
            f"MCP tool response: tool=read_content project={active_project.name} "
            f"path={url} type={type(file_content).__name__}"
        )
        match file_content:
            case TextFile(text=text, content_type=content_type):
                return {
                    "type": "text",
                    "text": text,
                    "content_type": content_type,
                    "encoding": "utf-8",
                }
            case ImageFile(jpeg=jpeg):
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("utf-8"),
                    },
                }
            case DocumentFile(data=data, content_type=content_type):
                return {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": content_type,
                        "data": base64.b64encode(data).decode("utf-8"),
                    },
                }
            case UnsupportedImage():
                return {
                    "type": "error",
                    "error": f"Unsupported image format for {path}: only PNG, JPEG and WebP can be shown",
                }
            case DocumentTooLarge(size=size):  # pragma: no cover
                return {
                    "type": "error",
                    "error": f"Document size {size} bytes exceeds maximum allowed size",
                }
