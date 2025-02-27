"""Guide prompts for Basic Memory MCP server.

These prompts help users and AI assistants understand how to use
Basic Memory effectively.
"""

from typing import Optional, List, Annotated

from loguru import logger
import logfire
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.notes import read_note
from basic_memory.mcp.tools.memory import build_context


@mcp.prompt(
    name="basic_memory_guide",
    description="Get guidance on how to use Basic Memory tools effectively",
)
async def basic_memory_guide(
    focus: Annotated[
        Optional[str], Field(description="Optional area to focus on (writing, context, search, etc.)")
    ] = None,
) -> str:
    """Return a concise guide on Basic Memory tools and how to use them.
    
    Args:
        focus: Optional area to focus on ("writing", "context", "search", etc.)
        
    Returns:
        A focused guide on Basic Memory usage.
    """
    with logfire.span("Getting Basic Memory guide", focus=focus):  # pyright: ignore
        logger.info(f"Getting Basic Memory guide, focus: {focus}")
        
        # Base path for guides
        base_path = "docs"
        
        # Default to main guide
        guide_path = f"{base_path}/ai-assistant-guide"
        
        # If focus specified, try to get more specific guide
        if focus:
            specific_guide_path = None
            if focus.lower() in ["write", "writing", "create", "notes"]:
                specific_guide_path = f"{base_path}/write-files"
            elif focus.lower() in ["search", "find", "query"]:
                specific_guide_path = f"{base_path}/search"
            elif focus.lower() in ["context", "memory", "uri", "build"]:
                specific_guide_path = f"{base_path}/build-context"
            elif focus.lower() in ["canvas", "diagram", "visualize"]:
                specific_guide_path = f"{base_path}/canvas"
            
            if specific_guide_path:
                try:
                    focused_guide = await read_note(specific_guide_path)
                    return _format_guide(focused_guide, focus)
                except Exception as e:
                    logger.warning(f"Error retrieving specific guide: {e}")
                    # Fall back to main guide if specific one not found
        
        try:
            # Get the main AI Assistant Guide
            guide_content = await read_note(guide_path)
            return _format_guide(guide_content, "general")
        except Exception as e:
            logger.error(f"Error retrieving guide: {e}")
            return _fallback_guide()


def _format_guide(content: str, focus: str) -> str:
    """Format guide content with helpful header and footer.
    
    Args:
        content: The guide content
        focus: The focus area
        
    Returns:
        Formatted guide with helpful context
    """
    header = f"""# Basic Memory Guide: {focus.title() if focus != "general" else "Getting Started"}

Below is guidance on how to use Basic Memory tools effectively{" for " + focus if focus != "general" else ""}.
This will help you understand how to read, write, and navigate knowledge through the Model Context Protocol.

---

"""
    
    footer = """

---

## Next Steps

You can:
- Use `build_context("memory://docs/documentation-index")` to explore all documentation
- Try `search({"text": "example"})` to find example usage
- Ask for more specific guidance with `basic_memory_guide(focus="search")`

Feel free to ask questions about using any of these tools!
"""
    
    return header + content + footer


def _fallback_guide() -> str:
    """Provide a minimal guide when other guides can't be loaded."""
    return """# Basic Memory Quick Reference

## Core Tools
- `write_note(title, content, folder, tags)` - Create or update notes
- `read_note(identifier)` - Read notes by title, path, or pattern
- `build_context(url)` - Build context from memory:// URL
- `search(query)` - Search across knowledge
- `recent_activity()` - See recent changes
- `canvas(layout, entities)` - Create visualization canvases

## Common Patterns
- Use memory:// URLs to navigate content
  - `memory://path/to/note` - Specific note
  - `memory://path/*` - All notes in path
  - `memory://path/relation-type/*` - Follow relations
- Write semantic markdown with categories and relations:
  - `- [category] Observation text #tag`
  - `- relation_type [[Entity]]`

## Examples

```python
# Write a new note
await write_note(
    title="Project Plan",
    content="# Project Plan\n\n## Observations\n- [goal] Complete v1 by April\n- [requirement] Support markdown",
    folder="projects",
    tags=["planning", "v1"]
)

# Read a note by title
content = await read_note("Project Plan")

# Build context from note
context = await build_context("memory://projects/project-plan")

# Search for related content
results = await search({"text": "requirement"})

# Check recent activity
activity = await recent_activity(timeframe="1 week")
```

For more guidance, try searching the documentation with `search({"text": "guide"})`.
"""