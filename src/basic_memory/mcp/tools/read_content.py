"""File reading tool for Basic Memory MCP server.

This module provides tools for reading raw file content directly,
supporting various file types including text, images, and other binary files.
Files are read directly without any knowledge graph processing.
"""

from typing import Optional
import base64
import io

from loguru import logger
from PIL import Image as PILImage

from basic_memory.mcp.server import mcp
from basic_memory.mcp.async_client import client
from basic_memory.mcp.tools.utils import call_get
from basic_memory.mcp.project_session import get_active_project
from basic_memory.schemas.memory import memory_url_path


def calculate_target_params(content_length):
    """Calculate initial quality and size based on input file size"""
    target_size = 350000  # Reduced target for more safety margin
    ratio = content_length / target_size

    logger.debug(
        "Calculating target parameters",
        content_length=content_length,
        ratio=ratio,
        target_size=target_size,
    )

    if ratio > 4:
        # Very large images - start very aggressive
        return 50, 600  # Lower initial quality and size
    elif ratio > 2:
        return 60, 800
    else:
        return 70, 1000


def resize_image(img, max_size):
    """Resize image maintaining aspect ratio"""
    original_dimensions = {"width": img.width, "height": img.height}

    if img.width > max_size or img.height > max_size:
        ratio = min(max_size / img.width, max_size / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        logger.debug("Resizing image", original=original_dimensions, target=new_size, ratio=ratio)
        return img.resize(new_size, PILImage.Resampling.LANCZOS)

    logger.debug("No resize needed", dimensions=original_dimensions)
    return img


def optimize_image(img, content_length, max_output_bytes=350000):
    """Iteratively optimize image with aggressive size reduction"""
    stats = {
        "dimensions": {"width": img.width, "height": img.height},
        "mode": img.mode,
        "estimated_memory": (img.width * img.height * len(img.getbands())),
    }

    initial_quality, initial_size = calculate_target_params(content_length)

    logger.debug(
        "Starting optimization",
        image_stats=stats,
        content_length=content_length,
        initial_quality=initial_quality,
        initial_size=initial_size,
        max_output_bytes=max_output_bytes,
    )

    quality = initial_quality
    size = initial_size

    # Convert to RGB if needed
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGB")
        logger.debug("Converted to RGB mode")

    iteration = 0
    min_size = 300  # Absolute minimum size
    min_quality = 20  # Absolute minimum quality

    while True:
        iteration += 1
        buf = io.BytesIO()
        resized = resize_image(img, size)

        resized.save(
            buf,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )

        output_size = buf.getbuffer().nbytes
        reduction_ratio = output_size / content_length

        logger.debug(
            "Optimization attempt",
            iteration=iteration,
            quality=quality,
            size=size,
            output_bytes=output_size,
            target_bytes=max_output_bytes,
            reduction_ratio=f"{reduction_ratio:.2f}",
        )

        if output_size < max_output_bytes:
            logger.info(
                "Image optimization complete",
                final_size=output_size,
                quality=quality,
                dimensions={"width": resized.width, "height": resized.height},
                reduction_ratio=f"{reduction_ratio:.2f}",
            )
            return buf.getvalue()

        # Very aggressive reduction for large files
        if content_length > 2000000:  # 2MB+   # pragma: no cover
            quality = max(min_quality, quality - 20)
            size = max(min_size, int(size * 0.6))
        elif content_length > 1000000:  # 1MB+ # pragma: no cover
            quality = max(min_quality, quality - 15)
            size = max(min_size, int(size * 0.7))
        else:
            quality = max(min_quality, quality - 10)  # pragma: no cover
            size = max(min_size, int(size * 0.8))  # pragma: no cover

        logger.debug("Reducing parameters", new_quality=quality, new_size=size)  # pragma: no cover

        # If we've hit minimum values and still too big
        if quality <= min_quality and size <= min_size:  # pragma: no cover
            logger.warning(
                "Reached minimum parameters",
                final_size=output_size,
                over_limit_by=output_size - max_output_bytes,
            )
            return buf.getvalue()


@mcp.tool(description="Read a file's raw content by path or permalink")
async def read_content(path: str, project: Optional[str] = None) -> dict:
    """Read a file's raw content by path or permalink.

    This tool provides direct access to file content in the knowledge base,
    handling different file types appropriately:
    - Text files (markdown, code, etc.) are returned as plain text
    - Images are automatically resized/optimized for display
    - Other binary files are returned as base64 if below size limits

    Args:
        path: The path or permalink to the file. Can be:
            - A regular file path (docs/example.md)
            - A memory URL (memory://docs/example)
            - A permalink (docs/example)
        project: Optional project name to read from. If not provided, uses current active project.

    Returns:
        A dictionary with the file content and metadata:
        - For text: {"type": "text", "text": "content", "content_type": "text/markdown", "encoding": "utf-8"}
        - For images: {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "base64_data"}}
        - For other files: {"type": "document", "source": {"type": "base64", "media_type": "content_type", "data": "base64_data"}}
        - For errors: {"type": "error", "error": "error message"}

    Examples:
        # Read a markdown file
        result = await read_file("docs/project-specs.md")

        # Read an image
        image_data = await read_file("assets/diagram.png")

        # Read using memory URL
        content = await read_file("memory://docs/architecture")

        # Read from specific project
        content = await read_content("docs/example.md", project="work-project")
    """
    logger.info("Reading file", path=path)

    active_project = get_active_project(project)
    project_url = active_project.project_url

    url = memory_url_path(path)
    response = await call_get(client, f"{project_url}/resource/{url}")
    content_type = response.headers.get("content-type", "application/octet-stream")
    content_length = int(response.headers.get("content-length", 0))

    logger.debug("Resource metadata", content_type=content_type, size=content_length, path=path)

    # Handle text or json
    if content_type.startswith("text/") or content_type == "application/json":
        logger.debug("Processing text resource")
        return {
            "type": "text",
            "text": response.text,
            "content_type": content_type,
            "encoding": "utf-8",
        }

    # Handle images
    elif content_type.startswith("image/"):
        logger.debug("Processing image")
        img = PILImage.open(io.BytesIO(response.content))
        img_bytes = optimize_image(img, content_length)

        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(img_bytes).decode("utf-8"),
            },
        }

    # Handle other file types
    else:
        logger.debug(f"Processing binary resource content_type {content_type}")
        if content_length > 350000:  # pragma: no cover
            logger.warning("Document too large for response", size=content_length)
            return {
                "type": "error",
                "error": f"Document size {content_length} bytes exceeds maximum allowed size",
            }
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": content_type,
                "data": base64.b64encode(response.content).decode("utf-8"),
            },
        }
