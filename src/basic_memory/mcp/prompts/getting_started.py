"""Getting-started prompt for Basic Memory MCP server.

Surfaced as a visible prompt / slash entry in MCP clients, this is the one onboarding
affordance we can plant in a client whose chrome we do not control. A user who just connected
from a connector directory can pick "Getting started with Basic Memory" and get oriented
instead of facing an empty tool list with no next step.

Stays offer-not-act: it guides the model to *offer* a first note and wait for the user's
go-ahead, never to write one unprompted, so it is safe to invoke in any session.
"""

from textwrap import dedent
from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.recent_activity import recent_activity


@mcp.prompt(
    name="getting_started",
    description="Introduce Basic Memory and help the user create their first note",
)
async def getting_started(
    project: Annotated[
        Optional[str],
        Field(description="Project to check for existing notes (optional)"),
    ] = None,
) -> str:
    """Return an onboarding guide for a newly-connected user.

    Embeds current recent activity and lets the model branch its own response on whether notes
    already exist, rather than asserting emptiness from a timeframe-limited call. The write path
    is always framed as an offer the user must accept, in the inspected project.

    Args:
        project: Project to inspect for existing notes and target for the first note (optional)

    Returns:
        A short onboarding guide.
    """
    logger.info("Rendering getting_started prompt")

    # Show current activity, but do NOT decide "empty" from it: recent_activity is
    # timeframe-limited, so an established base that is simply quiet looks identical to a
    # brand-new one. Let the model judge from the activity output (which already carries its own
    # empty-state guidance) and branch its own response, rather than asserting emptiness here.
    activity_text = str(await recent_activity(timeframe="30d", project=project))

    # Every example call keeps the project the prompt actually inspected. Omitting project lets
    # the tool fall back to its default/discovery path, which would silently move onboarding into
    # a different project than the one whose activity we just showed. `project_arg` is the leading
    # keyword form; `project_suffix` trails a positional first argument (read_note).
    project_arg = f'project="{project}", ' if project else ""
    project_suffix = f', project="{project}"' if project else ""

    return dedent(
        f"""
        # Getting started with Basic Memory

        Basic Memory gives the user a personal knowledge base: local markdown notes that persist
        across conversations, which both the user and their AI assistants can read and write.
        Anything saved here is available in future chats and in the Basic Memory app.

        Here is their recent activity:

        {activity_text}

        Based on what you see above:
        - **If they have no notes yet**, briefly explain that shared-memory loop and offer to
          save something useful from this conversation as their first note.
          Wait for them to say yes before writing anything:
          ```
          write_note({project_arg}title="...", content="...", folder="notes")
          ```
        - **If they already have notes**, help them keep the loop going:
          `read_note("permalink"{project_suffix})` to open one,
          `search_notes({project_arg}query="...")` to find a topic, or
          `write_note({project_arg}...)` to capture something new (offer first; don't write
          unprompted).
        """
    ).strip()
