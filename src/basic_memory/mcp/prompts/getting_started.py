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

    Checks recent activity so a returning user is welcomed back rather than pitched a first
    note. The write path is always framed as an offer the user must accept.

    Args:
        project: Project to inspect for existing notes (optional)

    Returns:
        A short onboarding guide tailored to whether the knowledge base has content yet.
    """
    logger.info("Rendering getting_started prompt")

    # Orient against existing content so an established base is welcomed back instead of being
    # pitched a "first note." recent_activity returns an error/guidance string rather than
    # raising, so a substring check is sufficient and avoids a second failure surface.
    activity_text = str(await recent_activity(timeframe="30d", project=project))
    looks_empty = "No recent activity" in activity_text

    if looks_empty:
        return dedent(
            """
            # Getting started with Basic Memory

            Basic Memory gives the user a personal knowledge base: local markdown notes that
            persist across conversations, which both the user and their AI assistants can read
            and write. Anything saved here is available in future chats and in the Basic Memory
            app.

            It looks like there are no notes yet. Do this now:

            1. Briefly explain that shared-memory loop to the user in your own words.
            2. Offer to save something useful from this conversation as their first note, and
               **wait for them to say yes before writing anything**:
               ```
               write_note(title="...", content="...", folder="notes")
               ```
            3. After it is saved, mention they can find it in the Basic Memory app and that you
               can read or update it in any future conversation.

            Do not create a note unprompted — offer first.
            """
        ).strip()

    return dedent(
        f"""
        # Getting started with Basic Memory

        This user already has notes in Basic Memory, so help them keep the loop going rather
        than starting over.

        Recent activity:

        {activity_text}

        Good next moves:
        - `read_note("permalink")` to open a specific note
        - `search_notes(query="...")` to find something by topic
        - `write_note(...)` to capture new decisions or insights from this conversation
          (offer first; don't write unprompted)
        """
    ).strip()
