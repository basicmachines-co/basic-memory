"""Refuse a move whose destination names another project (#881, #1607).

A destination such as 'other-project/file.md' reads as a move into another project,
which no move tool can do: the API would quietly create a nested folder named after
the project instead. Shared by move_note and bm_move.
"""

from pathlib import PurePosixPath, PureWindowsPath

from httpx import AsyncClient
from loguru import logger

from basic_memory.mcp.clients import DirectoryClient, ProjectClient
from basic_memory.schemas.directory import MAX_DIRECTORY_PAGE_SIZE
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.utils import generate_permalink, resolve_directory_casing


async def project_named_by_destination(
    client: AsyncClient, destination: str, active_project: ProjectItem
) -> str | None:
    """The other project a destination's first folder names, or None.

    None also when that folder already exists in the active project: a knowledge
    base organised by domain often has folders named after sibling projects.
    """
    project_list = await ProjectClient(client).list_projects()

    path_parts = [part for part in PureWindowsPath(destination).as_posix().split("/") if part]
    if not path_parts:
        return None
    leading_folder = path_parts[0]

    # Trigger: the first path segment matches a different project's name.
    # Why: a routing-style destination like "other-project/file.md" expresses an
    #      intent to move into another project, which no move tool can do — it
    #      would silently create a same-project nested folder instead.
    # Outcome: candidate for rejection, unless the folder is already local (below).
    # Projects are addressed by name or by permalink ("Other Project" is
    # "other-project"), so compare the generated permalinks too. A lone segment
    # with an extension is a root filename, not a folder: generate_permalink
    # would strip ".md" and turn "other-project.md" into a project prefix.
    is_root_filename = len(path_parts) == 1 and bool(PurePosixPath(leading_folder).suffix)
    leading_permalink = None if is_root_filename else generate_permalink(leading_folder)
    matching_project = next(
        (
            p.name
            for p in project_list.projects
            if (
                p.name.lower() == leading_folder.lower()
                or generate_permalink(p.name) == leading_permalink
            )
            and p.name.lower() != active_project.name.lower()
        ),
        None,
    )
    if matching_project is None:
        return None

    # Trigger: the leading folder already holds content in the active project.
    # Why: a knowledge base organised by domain routinely has folders named after
    #      sibling projects; an existing local folder is evidence of local intent,
    #      and rejecting it left `git mv` as the only way in (#1607).
    # Outcome: the move proceeds as an ordinary same-project move.
    # Trigger: the folder lookup itself fails after a project name matched.
    # Why: the name match already signals routing intent; allowing the move
    #      unverified is the silent misroute this guard exists to prevent.
    # Outcome: keep the cross-project rejection.
    # The server resolves a destination folder to an existing folder's casing
    # (#1326), so "schemas/" lands in "Schemas/"; resolve the same way here.
    try:
        directory_client = DirectoryClient(client, active_project.external_id)
        root_folders: list[str] = []
        page = 1
        while True:
            listing = await directory_client.list(
                "/", depth=1, page=page, page_size=MAX_DIRECTORY_PAGE_SIZE
            )
            root_folders.extend(node.name for node in listing.nodes if node.type == "directory")
            # The default listing orders folders before files, so the first page
            # holding a file has already yielded every root folder.
            if not listing.has_more or any(node.type != "directory" for node in listing.nodes):
                break
            page += 1
    except Exception as e:
        logger.debug(f"Could not verify local folder {leading_folder!r}: {e}")
    else:
        if resolve_directory_casing(leading_folder, root_folders) in root_folders:
            return None
    return matching_project
