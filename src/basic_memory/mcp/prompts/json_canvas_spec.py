from pathlib import Path

import logfire
from loguru import logger

from basic_memory.mcp.server import mcp


@mcp.resource(
    uri="memory://json_canvas_spec",
    name="json_canvas_spec",
    description="JSON Canvas specification for visualizing knowledge graphs in Obsidian"
)
def json_canvas_spec() -> str:
    """Return the JSON Canvas specification for Obsidian visualizations.
    
    Returns:
        The JSON Canvas specification document.
    """
    with logfire.span("Getting JSON Canvas spec"):  # pyright: ignore
        logger.info("Loading JSON Canvas spec resource")
        canvas_spec = Path(__file__).parent.parent.parent.parent.parent / "data/json_canvas_spec_1_0.md"
        content = canvas_spec.read_text()
        logger.info(f"Loaded JSON Canvas spec ({len(content)} chars)")
        return content