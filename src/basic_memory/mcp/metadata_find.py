"""Find notes by frontmatter, with optional field projection.

One search call answers the filters, scoped server-side by file path. With
`fields`, each hit is projected down to the note's identity plus the requested
frontmatter values, read from the accepted Markdown so YAML types survive.
"""

from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError
from pydantic import TypeAdapter

from basic_memory.file_utils import ParseError, has_frontmatter, parse_frontmatter
from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.mcp.note_targets import ProjectTarget
from basic_memory.repository.metadata_filters import MetadataPath, parse_metadata_path
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchResult

FRONTMATTER_JSON_ADAPTER = TypeAdapter(dict[str, Any])

# A projected row is the note's identity plus the requested fields. A whole search
# hit also carries up to 4000 characters of the note body, which is the cost
# `fields` exists to remove, so the row is a whitelist: a hit that grows another
# column cannot leak into it.
PROJECTED_ROW_KEYS = ("title", "permalink", "file_path", "external_id", "updated_at")


def parse_field_paths(fields: list[str]) -> list[MetadataPath]:
    """Parse `fields` entries into frontmatter paths, refusing malformed ones.

    A malformed path ('review..approved', '.owner') would project to null for every
    hit and read exactly like a field the notes do not carry, so it is refused here
    through the same parse the filter keys use.
    """
    names = [field_name.strip() for field_name in fields]
    if not names or any(not field_name for field_name in names):
        raise ValueError("'fields' entries must be non-empty frontmatter field names")
    paths = []
    for field_name in names:
        field_path = parse_metadata_path(field_name)
        if field_path is None:
            raise ValueError(
                f"malformed field path '{field_name}'; field paths are dot-separated "
                "names of letters, digits, '_' or '-' (e.g. 'title' or 'review.approved')"
            )
        paths.append(field_path)
    return paths


# --- The search ---


@dataclass(frozen=True, slots=True)
class MetadataSearch:
    """Frontmatter filters split into what the search API takes for each.

    The note type is matched on the normalized `note_type` column, the same value
    search hits display, so a type copied from a hit finds its notes again (#1428).
    The other filters run as frontmatter predicates.
    """

    note_types: list[str] | None
    filters: dict[str, Any]


def split_type_filter(filters: dict[str, Any]) -> MetadataSearch:
    """Move an equality or `$in` filter on `type` onto the note_type column."""
    match filters.get("type"):
        case str() as note_type:
            note_types = [note_type]
        case {"$in": [*values]} if values and all(isinstance(value, str) for value in values):
            note_types = values
        case _:
            # Other operators ($exists, $gt, ...) stay frontmatter predicates.
            return MetadataSearch(note_types=None, filters=filters)
    rest = {key: value for key, value in filters.items() if key != "type"}
    return MetadataSearch(note_types=note_types, filters=rest)


# --- Field projection ---


def project_metadata_fields(metadata: dict[str, Any], fields: list[MetadataPath]) -> dict[str, Any]:
    """Pick the requested fields out of a note's metadata, keyed by the field name.

    Dot-paths walk nested mappings. A missing key or a non-mapping on the way is
    None, never a dropped row: null is a real answer here, which is why the
    paths are parsed before any of this runs.
    """
    projected: dict[str, Any] = {}
    for field in fields:
        value: Any = metadata
        for part in field.parts:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        projected[field.key] = value
    return projected


def projection_metadata(content: str | None, identity: dict[str, Any]) -> dict[str, Any]:
    """A note's frontmatter with YAML types intact, over its indexed identity.

    `identity` is the title and type the index holds; they win over the
    frontmatter's own, because the index is what search displays and filters on.
    A note without frontmatter (or with malformed frontmatter, or a non-Markdown
    file with no accepted content) projects its identity alone. The JSON-mode
    dump keeps numbers, booleans, lists and mappings typed and spells YAML dates
    as ISO strings, the only form JSON can carry.
    """
    if content is None or not has_frontmatter(content):
        return identity
    try:
        authored = parse_frontmatter(content)
    except ParseError:
        return identity
    return FRONTMATTER_JSON_ADAPTER.dump_python({**authored, **identity}, mode="json")


def _hit_external_id(hit: SearchResult) -> str:
    if hit.external_id is None:
        raise ToolError("Search hits carry no external_id; this server is too old for `fields`.")
    return hit.external_id


def _hit_identity(hit: SearchResult) -> dict[str, Any]:
    identity: dict[str, Any] = {"title": hit.title}
    note_type = (hit.metadata or {}).get("note_type")
    if note_type is not None:
        identity["type"] = note_type
    return identity


async def find_by_metadata(
    target: ProjectTarget,
    *,
    metadata_filters: dict[str, Any],
    fields: list[MetadataPath] | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """One search for notes matching the filters, under the target's path.

    The path scope is `target.route.path`; `SearchQuery.file_path_prefix`
    normalizes it and maps every root spelling to "no scope". It is a prefix of
    the indexed file path, where the note lives, not of its permalink, and it ANDs
    with the filters server-side, so the reported total is the real count and
    every page of it is reachable.
    """
    search = split_type_filter(metadata_filters)
    query = SearchQuery(
        file_path_prefix=target.route.path,
        metadata_filters=search.filters,
        note_types=search.note_types,
        entity_types=[SearchItemType.ENTITY],
    )
    response = await SearchClient(target.client, target.project.external_id).search(
        query.model_dump(), page=page, page_size=page_size
    )
    # Qualified before the rows are read, so projected and unprojected rows carry
    # the same addresses.
    payload = target.paths.search(response.model_dump(mode="json", exclude_none=True))
    if not fields:
        return payload

    hit_ids = [_hit_external_id(hit) for hit in response.results]
    contents = await KnowledgeClient(
        target.client, target.project.external_id
    ).accepted_note_contents(hit_ids)
    # `fields` is set after the dump, so a null value survives exclude_none.
    payload["results"] = [
        {
            **{key: row[key] for key in PROJECTED_ROW_KEYS if key in row},
            "fields": project_metadata_fields(
                projection_metadata(contents.get(hit_id), _hit_identity(hit)), fields
            ),
        }
        for row, hit, hit_id in zip(payload["results"], response.results, hit_ids, strict=True)
    ]
    return payload
