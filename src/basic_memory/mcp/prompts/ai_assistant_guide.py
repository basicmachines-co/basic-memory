from pathlib import Path

import logfire
from loguru import logger

from basic_memory.mcp.server import mcp


@mcp.resource(
    uri="memory://ai_assistant_guide",
    name="ai assistant guide",
    description="Give an AI assistant guidance on how to use Basic Memory tools effectively",
)
@logfire.instrument(extract_args=False)
def ai_assistant_guide() -> str:
    """Return a concise guide on Basic Memory tools and how to use them.

    Args:
        focus: Optional area to focus on ("writing", "context", "search", etc.)

    Returns:
        A focused guide on Basic Memory usage.
    """
    logger.info("Loading AI assistant guide resource")
    guide_doc = Path(__file__).parent.parent.parent.parent.parent / "data/ai_assistant_guide.md"
    content = guide_doc.read_text()
    logger.info(f"Loaded AI assistant guide ({len(content)} chars)")
    return content
