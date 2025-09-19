from pathlib import Path

from basic_memory.config import ConfigManager
from basic_memory.mcp.server import mcp
from loguru import logger


@mcp.resource(
    uri="memory://ai_assistant_guide",
    name="ai assistant guide",
    description="Give an AI assistant guidance on how to use Basic Memory tools effectively",
)
def ai_assistant_guide() -> str:
    """Return a concise guide on Basic Memory tools and how to use them.

    Dynamically adapts instructions based on configuration:
    - Default project mode: Simplified instructions with automatic project
    - Regular mode: Project discovery and selection guidance
    - CLI constraint mode: Single project constraint information

    Returns:
        A focused guide on Basic Memory usage.
    """
    logger.info("Loading AI assistant guide resource")

    # Load base guide content
    guide_doc = Path(__file__).parent.parent / "resources" / "ai_assistant_guide.md"
    content = guide_doc.read_text(encoding="utf-8")

    # Check configuration for mode-specific instructions
    config = ConfigManager().config

    # Add mode-specific header
    mode_info = ""
    if config.default_project_mode:
        mode_info = f"""
# 🎯 Default Project Mode Active

**Current Configuration**: All operations automatically use project '{config.default_project}'

**Simplified Usage**: You don't need to specify the project parameter in tool calls.
- `write_note(title="Note", content="...", folder="docs")` ✅
- Project parameter is optional and will default to '{config.default_project}'
- To use a different project, explicitly specify: `project="other-project"`

────────────────────────────────────────

"""
    else:
        mode_info = """
# 🔧 Multi-Project Mode Active

**Current Configuration**: Project parameter required for all operations

**Project Discovery Required**: Use these tools to select a project:
- `list_memory_projects()` - See all available projects
- `recent_activity()` - Get project activity and recommendations
- Remember the user's project choice throughout the conversation

────────────────────────────────────────

"""

    # Prepend mode info to the guide
    enhanced_content = mode_info + content

    logger.info(
        f"Loaded AI assistant guide ({len(enhanced_content)} chars) with mode: {'default_project' if config.default_project_mode else 'multi_project'}"
    )
    return enhanced_content
