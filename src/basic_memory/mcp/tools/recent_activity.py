"""Recent activity for bm_project(command="recent").

One project's recently changed notes, observations and relations, rendered as text
or returned as rows with paging for JSON callers.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from fastmcp import Context
from loguru import logger

from basic_memory.mcp.index_readiness import project_index_required
from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.tool_params import ItemKind, search_item_types
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.memory import GraphContext
from basic_memory.schemas.search import SearchItemType


@dataclass(frozen=True, slots=True)
class RecentRows:
    """One page of recent activity as rows, and whether another page follows."""

    rows: list[dict[str, Any]]
    page: int
    page_size: int
    has_more: bool


async def recent_activity_page(
    *,
    items: list[ItemKind] | None,
    timeframe: str,
    page: int,
    page_size: int,
    project: str | None,
    project_id: str | None,
    output_format: Literal["text", "json"],
    context: Context | None,
) -> str | RecentRows:
    """Recent activity as text, or as rows with paging for JSON callers.

    A str is either the rendered text or, for a project that was never indexed,
    the indexing guidance in place of an empty result.
    """
    # Trigger: the caller named no item kinds.
    # Why: one well-connected note would otherwise fill the page with its own
    #      observations and relations.
    # Outcome: whole notes only.
    item_types = search_item_types(items) or [SearchItemType.ENTITY]
    params: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "max_related": 10,
        "depth": 1,
        "timeframe": timeframe,
        "type": [item_type.value for item_type in item_types],
    }

    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        logger.debug(
            f"Getting recent activity from project {active_project.name}: "
            f"items={items}, timeframe={timeframe}"
        )
        response = await call_get(
            client,
            f"/v2/projects/{active_project.external_id}/memory/recent",
            params=params,
        )
        activity_data = GraphContext.model_validate(response.json())

        # Do not offer first-note onboarding before the project was indexed.
        if not activity_data.results:
            guidance = await project_index_required(client, active_project)
            if guidance is not None:
                return guidance

    if output_format == "json":
        return RecentRows(
            rows=_extract_recent_rows(activity_data),
            page=page,
            page_size=page_size,
            has_more=activity_data.has_more,
        )

    # Pass the external id so onboarding examples route by id, not by a name that
    # can collide across cloud workspaces.
    return _format_project_output(
        active_project.name,
        activity_data,
        timeframe,
        page=page,
        project_id=active_project.external_id,
        type_filter_applied=bool(items),
    )


def _extract_recent_rows(activity_data: GraphContext) -> list[dict[str, Any]]:
    """Flatten GraphContext into a list of recent rows."""
    rows: list[dict[str, Any]] = []
    for result in activity_data.results:
        primary = result.primary_result
        rows.append(
            {
                "type": primary.type,
                "title": primary.title,
                "permalink": primary.permalink,
                "file_path": primary.file_path,
                "created_at": primary.created_at.isoformat() if primary.created_at else None,
            }
        )
    return rows


def _format_project_output(
    project_name: str,
    activity_data: GraphContext,
    timeframe: str,
    *,
    page: int,
    project_id: str,
    type_filter_applied: bool,
) -> str:
    """Format one project's recent activity as human-readable text.

    `project_id` is the project's route-safe external id. Onboarding examples use it
    instead of the display name so a model following them cannot land in a different
    same-named project in another cloud workspace.
    """
    lines = [f"## Recent Activity: {project_name} ({timeframe})"]

    if not activity_data.results:
        # Trigger: the requested activity page contains no rows.
        # Why: filtered and later-page misses do not establish that the project has no notes.
        # Outcome: only an unfiltered first-page orientation offers first-note onboarding.
        if page > 1:
            lines.append(
                f"\nNo recent activity was found on page {page} for "
                f"'{project_name}' within {timeframe}."
            )
            lines.append(f"Try page={page - 1} or return to page=1.")
            return "\n".join(lines)

        if type_filter_applied:
            lines.append(
                f"\nNo recent activity matched the requested type filter in "
                f"'{project_name}' within {timeframe}."
            )
            lines.append("Try another type or omit `items` to see recent notes and documents.")
            return "\n".join(lines)

        lines.append(f"\nNo recent activity in '{project_name}' within {timeframe}.")
        # Recent activity is the orientation call a model makes at session start, so an empty
        # result is the natural moment to help. Stay offer-not-act, and cover both a brand-new
        # (empty) knowledge base and an established one that is merely quiet in this window.
        # Examples name the external id: names collide across cloud workspaces, ids do not.
        route = f'project="{project_id}"'
        lines.append("")
        lines.append(
            "If the user is just getting started and has no notes yet, briefly explain that "
            "Basic Memory keeps notes that persist across conversations and are shared between "
            "the user and their AI, then offer to save something useful from this conversation "
            "as their first note — wait for them to agree before writing:"
        )
        lines.append("```")
        lines.append(f'bm_write({route}, title="...", content="...", directory="notes")')
        lines.append("```")
        lines.append(
            f"Otherwise, widen the window with "
            f'bm_project(command="recent", {route}, since="30d") or find a topic '
            f'with bm_search({route}, query="...").'
        )
        return "\n".join(lines)

    # Group results by type
    entities = []
    relations = []
    observations = []

    for result in activity_data.results:
        if result.primary_result.type == "entity":
            entities.append(result.primary_result)
        elif result.primary_result.type == "relation":
            relations.append(result.primary_result)
        elif result.primary_result.type == "observation":
            observations.append(result.primary_result)

    # Show entities (notes/documents). Render every row the API returned —
    # `page_size` is the single knob for how much comes back, so heading count
    # and body row count always agree (regression: #784 silent truncation).
    if entities:
        lines.append(f"\n**📄 Recent Notes & Documents ({len(entities)}):**")
        for entity in entities:
            title = entity.title or "Untitled"
            # Get folder from file_path
            folder = ""
            if entity.file_path:
                folder_path = str(PurePosixPath(entity.file_path).parent)
                if folder_path and folder_path != ".":
                    folder = f" ({folder_path})"
            # external_id makes rows linkable: hosted MCP appends a web-app
            # link template that the agent fills with this id on request.
            lines.append(f"  • {title}{folder} [id: {entity.external_id}]")

    # Show observations (categorized insights)
    if observations:
        lines.append(f"\n**🔍 Recent Observations ({len(observations)}):**")
        # Group by category
        by_category = {}
        for obs in observations[:10]:  # Limit to recent ones
            category = obs.category
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(obs)

        for category, obs_list in list(by_category.items())[:5]:  # Show top 5 categories
            lines.append(f"  **{category}:** {len(obs_list)} items")
            for obs in obs_list[:2]:  # Show 2 examples per category
                content = obs.content
                # Truncate at word boundary
                if len(content) > 80:
                    content = _truncate_at_word(content, 80)
                lines.append(f"    - {content}")

    # Show relations (connections)
    if relations:
        lines.append(f"\n**🔗 Recent Connections ({len(relations)}):**")
        for rel in relations:
            rel_type = rel.relation_type
            from_entity = rel.from_entity or "Unknown"
            to_entity = rel.to_entity

            # Format as WikiLinks to show they're readable notes
            from_link = f"[[{from_entity}]]" if from_entity != "Unknown" else from_entity
            to_link = f"[[{to_entity}]]" if to_entity else "[Missing Link]"

            lines.append(f"  • {from_link} → {rel_type} → {to_link}")

    # Activity summary with pagination guidance
    total = len(activity_data.results)
    if activity_data.has_more:
        lines.append(
            f"\n**Activity Summary:** Showing {total} items (page {page}). "
            f"Use page={page + 1} to see more."
        )
    else:
        lines.append(f"\n**Activity Summary:** {total} items found.")

    return "\n".join(lines)


def _truncate_at_word(text: str, max_length: int) -> str:
    """Truncate text at word boundary."""
    if len(text) <= max_length:
        return text

    # Find last space before max_length
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")

    if last_space > max_length * 0.7:  # Only truncate at word if we're not losing too much
        return text[:last_space] + "..."
    else:
        return text[: max_length - 3] + "..."
