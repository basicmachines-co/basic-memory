from pathlib import Path

from loguru import logger

from basic_memory.mcp.server import mcp


@mcp.resource(
    uri="memory://ai_assistant_guide",
    name="ai assistant guide",
    description=(
        "Bootstrap an AI assistant on Basic Memory and explain how to fetch current documentation"
    ),
)
def ai_assistant_guide() -> str:
    """Return the bundled assistant bootstrap guide."""
    logger.info("Loading AI assistant guide resource")

    guide_doc = Path(__file__).parent.parent / "resources" / "ai_assistant_guide.md"
    content = guide_doc.read_text(encoding="utf-8")
    logger.info(f"Loaded AI assistant guide ({len(content)} chars)")
    return content
