from pathlib import Path

import logfire

from basic_memory.mcp.server import mcp


@mcp.resource(
    uri="memory://ai_assistant_guide",
    name="ai_assistant_guide",
    description="Give an AI assistant guidance on how to use Basic Memory tools effectively",
)
def ai_assistant_guide() -> str:
    """Return a concise guide on Basic Memory tools and how to use them.

    Args:
        focus: Optional area to focus on ("writing", "context", "search", etc.)

    Returns:
        A focused guide on Basic Memory usage.
    """
    with logfire.span("Getting Basic Memory guide"):  # pyright: ignore
        guide_doc = Path(__file__).parent.parent.parent.parent.parent / "data/ai_assistant_guide.md"
        return guide_doc.read_text()


@mcp.resource("spec://canvas")
def canvas_spec() -> str:
    """Static configuration data"""
    canvas_spec = Path(__file__).parent.parent.parent.parent.parent / "data/json_canvas_spec_1_0.md"
    return canvas_spec.read_text()