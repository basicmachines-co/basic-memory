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
from basic_memory.mcp.tools.project_management import list_memory_projects
from basic_memory.mcp.tools.recent_activity import recent_activity


type ProjectMetadata = dict[str, object]


def _project_string(project: ProjectMetadata, field: str) -> str | None:
    """Return a non-empty string field from structured project metadata."""
    value = project.get(field)
    return value if isinstance(value, str) and value else None


def _project_route_argument(project: ProjectMetadata) -> tuple[str, str] | None:
    """Return the tool argument that preserves the discovered project's route."""
    if _project_string(project, "source") == "local+cloud":
        project_name = _project_string(project, "name")
        if project_name:
            return "project", project_name

    project_id = _project_string(project, "external_id")
    return ("project_id", project_id) if project_id else None


def _select_project(
    projects: list[ProjectMetadata],
    *,
    requested_project: str | None,
    default_project: str | None,
    constrained_project: str | None,
) -> ProjectMetadata | None:
    """Select a project only when the listing identifies one unambiguous target."""
    eligible = [project for project in projects if _project_string(project, "external_id")]
    # A single-project server ignores caller-selected project arguments downstream. Keep the
    # prompt aligned with that enforced route instead of advertising another project's id.
    requested_identifier = constrained_project or requested_project

    if requested_identifier:
        exact_matches = [
            project
            for project in eligible
            if requested_identifier
            in {
                _project_string(project, "external_id"),
                _project_string(project, "qualified_name"),
            }
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        name_matches = [
            project
            for project in eligible
            if _project_string(project, "name") == requested_identifier
        ]
        return name_matches[0] if len(name_matches) == 1 else None

    default_matches = [
        project
        for project in eligible
        if project.get("is_default") is True
        and (default_project is None or _project_string(project, "name") == default_project)
    ]
    if len(default_matches) == 1:
        return default_matches[0]

    # A local+cloud row represents the configured project route. Preserve that route before
    # considering cloud workspace defaults when discovery marks both copies as default.
    configured_defaults = [
        project
        for project in default_matches
        if _project_string(project, "source") == "local+cloud"
    ]
    if len(configured_defaults) == 1:
        return configured_defaults[0]

    # Multiple workspaces can each mark a project as their default. The default
    # workspace is the only collision-safe implicit choice across those workspaces.
    workspace_defaults = [
        project for project in default_matches if project.get("workspace_is_default") is True
    ]
    if len(workspace_defaults) == 1:
        return workspace_defaults[0]

    return eligible[0] if len(eligible) == 1 else None


def _selection_guide(
    projects: list[ProjectMetadata],
    *,
    requested_project: str | None,
) -> str:
    """Guide the assistant to establish project scope before suggesting note actions."""
    introduction = dedent(
        """
        # Choose a Basic Memory project

        Basic Memory gives the user a personal knowledge base: Markdown notes that persist
        across conversations, which both the user and their AI assistants can read and write.
        """
    ).strip()

    if not projects:
        next_step = dedent(
            """
            No Basic Memory projects are available yet. Ask the user to create or connect a
            project first. Then call `list_memory_projects()` and continue with
            `recent_activity(project_id="...", timeframe="30d")` using the selected project's
            returned `external_id`.

            Do not read, search, or write notes until a concrete project has been selected.
            """
        ).strip()
        return f"{introduction}\n\n{next_step}"

    requested_message = (
        f'The requested project "{requested_project}" did not identify one unique project.\n\n'
        if requested_project
        else "No single default project could be selected.\n\n"
    )
    options = []
    for project in projects:
        name = _project_string(project, "qualified_name") or _project_string(project, "name")
        route_argument = _project_route_argument(project)
        if name and route_argument:
            argument_name, argument_value = route_argument
            options.append(f'- `{name}` (`{argument_name}="{argument_value}"`)')

    project_options = "\n".join(options) if options else "- Call `list_memory_projects()`"
    next_step = dedent(
        """
        Ask the user which project to use. Once they choose, continue with
        `recent_activity(...)` using the route argument listed for that choice, and keep that
        same argument in later note calls. Do not read, search, or write notes until one concrete
        project has been selected.
        """
    ).strip()
    return (
        f"{introduction}\n\n{requested_message}Available projects:\n"
        f"{project_options}\n\n{next_step}"
    )


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
    is always framed as an offer the user must accept, in a concretely resolved project.

    Args:
        project: Project to inspect for existing notes and target for the first note (optional)

    Returns:
        A short onboarding guide.
    """
    logger.info("Rendering getting_started prompt")

    project_listing = await list_memory_projects(output_format="json")
    if not isinstance(project_listing, dict):
        raise TypeError("Structured project listing returned text")

    project_rows = project_listing.get("projects")
    if not isinstance(project_rows, list):
        raise TypeError("Structured project listing is missing projects")

    projects = [item for item in project_rows if isinstance(item, dict)]
    default_project = project_listing.get("default_project")
    constrained_project = project_listing.get("constrained_project")
    selected_project = _select_project(
        projects,
        requested_project=project,
        default_project=default_project if isinstance(default_project, str) else None,
        constrained_project=(constrained_project if isinstance(constrained_project, str) else None),
    )
    if selected_project is None:
        return _selection_guide(projects, requested_project=project)

    route_argument = _project_route_argument(selected_project)
    if route_argument is None:
        raise ValueError("Selected project is missing a tool route")
    argument_name, argument_value = route_argument

    # Show current activity, but do NOT decide "empty" from it: recent_activity is
    # timeframe-limited, so an established base that is simply quiet looks identical to a
    # brand-new one. Let the model judge from the activity output (which already carries its own
    # empty-state guidance) and branch its own response, rather than asserting emptiness here.
    if argument_name == "project":
        activity = await recent_activity(timeframe="30d", project=argument_value)
    else:
        activity = await recent_activity(timeframe="30d", project_id=argument_value)
    activity_text = str(activity).strip()

    # Every example call keeps the exact route the prompt inspected. Cloud-only rows use their
    # collision-safe external id; merged local+cloud rows retain the configured project route.
    project_arg = f'{argument_name}="{argument_value}", '

    introduction = dedent(
        """
        # Getting started with Basic Memory

        Basic Memory gives the user a personal knowledge base: Markdown notes that persist
        across conversations, which both the user and their AI assistants can read and write.
        Anything saved here is available in future chats and wherever they open this project.

        Here is their recent activity:
        """
    ).strip()
    next_steps = dedent(
        f"""
        Based on what you see above:
        - **If they have no notes yet**, briefly explain that shared-memory loop and offer to
          save something useful from this conversation as their first note.
          Wait for them to say yes before writing anything:
          ```
          write_note({project_arg}title="...", content="...", directory="notes")
          ```
        - **If they already have notes**, help them keep the loop going:
          `read_note({project_arg}identifier="permalink")` to open one,
          `search_notes({project_arg}query="...")` to find a topic, or
          `write_note({project_arg}...)` to capture something new (offer first; don't write
          unprompted).
        """
    ).strip()
    return f"{introduction}\n\n{activity_text}\n\n{next_steps}"
