"""The result a bm_* tool returns after it changes a note (SPEC-35.2, #1831).

bm_write, bm_edit and bm_move report the same fields, so a caller reads the new
path and checksum from one shape whichever tool made the change.
"""

from typing import Any

from basic_memory.mcp.note_targets import qualify_note_paths
from basic_memory.mcp.project_context import ProjectPathRoute
from basic_memory.schemas.v2.entity import EntityResponseV2


def note_change_result(
    note: EntityResponseV2, *, action: str, route: ProjectPathRoute
) -> dict[str, Any]:
    """The JSON result for a changed note, with its path qualified like the request."""
    return qualify_note_paths(
        {
            "action": action,
            "title": note.title,
            "permalink": note.permalink,
            "file_path": note.file_path,
            # The accepted revision's checksum, which bm_write and bm_edit take back as
            # expected_checksum. file_checksum lags behind materialization (#1586).
            "checksum": note.db_checksum,
            "observations": len(note.observations),
            "relations": len(note.relations),
            "unresolved_relations": sum(1 for relation in note.relations if not relation.to_id),
        },
        route,
    )


def format_note_change(result: dict[str, Any], *, headline: str) -> list[str]:
    """The text result's lines: what changed, where, and the checksum to pass back."""
    relations = f"relations: {result['relations']}"
    if result["unresolved_relations"]:
        relations += f" ({result['unresolved_relations']} not yet resolved)"
    return [
        headline,
        f"title: {result['title']}",
        f"permalink: {result['permalink']}",
        f"checksum: {result['checksum']}",
        f"observations: {result['observations']}, {relations}",
    ]
