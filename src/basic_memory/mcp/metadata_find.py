"""Find notes by frontmatter, with optional field projection (#1830).

The metadata arm shared by bm_find and the legacy find tool. One search call
answers the filters, scoped server-side by file path; `fields` then projects each
hit down to the note's identity plus the requested frontmatter values, hydrated
from the canonical Markdown so YAML types survive.
"""

import asyncio
from typing import Any

from fastmcp.exceptions import ToolError
from pydantic import TypeAdapter

from basic_memory.file_utils import ParseError, has_frontmatter, parse_frontmatter
from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.mcp.note_targets import ProjectTarget, qualify_search_paths
from basic_memory.repository.metadata_filters import MetadataPath, parse_metadata_path
from basic_memory.schemas.search import SearchItemType, SearchQuery

# In-flight entity reads while projecting `find --fields`. The knowledge API has
# no bulk entity read, so a full page costs page_size GETs; this bounds how many
# are open at once — enough to hide per-request latency on a cloud-routed
# project, small enough not to flood the API with one tool call's fan-out.
# Deliberately not max_tokens-sliced: a slice param 404s on an entity with no
# markdown content (knowledge_router._apply_note_slice), which would turn a
# projected row into a failed find.
FIELD_PROJECTION_CONCURRENCY = 8

FRONTMATTER_JSON_ADAPTER = TypeAdapter(dict[str, Any])


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


def project_metadata_fields(
    entity_metadata: dict[str, Any] | None, fields: list[MetadataPath]
) -> dict[str, Any]:
    """Project requested frontmatter fields out of an entity's metadata.

    Field names are echoed verbatim as keys (dot-paths walk nested dicts). A
    missing key or non-dict intermediate yields None — never a dropped row.

    Takes parsed paths rather than strings because null here is a real answer
    ("this note has no such field"), so a malformed path that walked to null
    would be indistinguishable from data. Requiring MetadataPath moves that
    refusal to the one parse that can tell the two apart.
    """
    projected: dict[str, Any] = {}
    for field in fields:
        value: Any = entity_metadata
        for part in field.parts:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        projected[field.key] = value
    return projected


def projection_metadata(
    content: str | None, normalized_metadata: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Return frontmatter values with their YAML scalar and collection types intact.

    ``entity_metadata`` is the searchable projection and intentionally stores
    scalar values as strings for compatibility. Field projection is a read of
    the canonical note, so parse its frontmatter instead. Pydantic's JSON-mode
    dump keeps numbers, booleans, lists, and mappings typed while spelling YAML
    dates as ISO strings, which are the only representation JSON can carry.

    Start from indexed metadata so parser-supplied defaults and malformed or
    frontmatter-free notes retain their normal representation. Valid authored
    fields then replace those normalized values with their YAML-native types.
    """
    if content is None or not has_frontmatter(content):
        return normalized_metadata
    try:
        authored_metadata = parse_frontmatter(content)
    except ParseError:
        return normalized_metadata

    projection_metadata = dict(normalized_metadata or {})
    projection_metadata.update(authored_metadata)
    for required_string_field in ("title", "type"):
        if normalized_metadata and required_string_field in normalized_metadata:
            projection_metadata[required_string_field] = normalized_metadata[required_string_field]
    return FRONTMATTER_JSON_ADAPTER.dump_python(projection_metadata, mode="json")


# --- What a projected row carries ---
# `fields` is the SELECT to the predicates' WHERE, and a SELECT answers with the
# columns asked for. A whole SearchResult carries the note body too — up to
# SearchIndexRow.CONTENT_DISPLAY_LIMIT (4000) characters of it — so the 200-row
# inventory call the literary-analysis skill documents answered a request for two
# frontmatter values with most of a megabyte of prose, which is the exact cost
# `fields` exists to remove.
#
# A whitelist rather than a content blocklist: the row is the note's identity —
# how to name it (title), read it (permalink, file_path) and deep-link it
# (external_id, #1423) — plus when it last changed and the projection itself. A
# SearchResult that later grows another bulky column therefore cannot leak into a
# projected response. What is left out is a body (content, matched_chunk), a
# ranking no text query produced (score, -0.0 on every metadata hit), a second
# spelling of an identity already here (entity, entity_id), or index-row metadata
# a caller can name as a field instead ("type").
#
# Only projection mode narrows. Without `fields`, `meta` still answers with the
# full search response grep's renderers read, because there the hit *is* the
# answer.
PROJECTED_ROW_KEYS = ("title", "permalink", "file_path", "external_id", "updated_at")


def projected_row(
    row: dict[str, Any], entity_metadata: dict[str, Any] | None, fields: list[MetadataPath]
) -> dict[str, Any]:
    """One projected hit: the note's identity, plus the fields the caller asked for.

    Takes the already-dumped row so the identity values keep the response's own
    JSON serialization (`updated_at` as an ISO string), and `fields` is injected
    post-dump so a null field value survives the response's exclude_none.
    """
    projected = {key: row[key] for key in PROJECTED_ROW_KEYS if key in row}
    projected["fields"] = project_metadata_fields(entity_metadata, fields)
    return projected


async def find_by_metadata(
    target: ProjectTarget,
    *,
    metadata_filters: dict[str, Any],
    fields: list[MetadataPath] | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """The metadata arm of find: one search call, plus per-hit field projection.

    The listing arm's counterpart to ``find_listing``; `find` has already bounded
    the pagination and refused `name` and `depth` against `meta`.

    The path scope is ``target.route.path`` — the caller's input verbatim when no
    project prefix was recognized, so one value covers both routed and raw
    spellings. ``SearchQuery.file_path_prefix`` is the boundary parser for it: it
    reads "./specs" the way the directory listing does, and collapses every root
    spelling — including the "" a bare '<project>' routes to, a mount point
    rather than a subtree — onto "no scope". It composes server-side as a
    file-path prefix — the indexed `file_path`, which is where the note actually
    lives, not its permalink, which stops mirroring that path once a note pins
    one in frontmatter or is moved with update_permalinks_on_move disabled (the
    default). It ANDs with the metadata filters in the same WHERE, so the total
    the server reports is the real match count for the scope that ran, and every
    page of it is reachable.
    """
    client, active_project, route = target.client, target.project, target.route

    # Type equality/membership must use the same normalization as the type
    # displayed in search hits. Raw frontmatter comparisons made those
    # displayed values fail when fed back into find (#1428).
    note_types: list[str] | None = None
    type_filter = metadata_filters.get("type")
    if isinstance(type_filter, str):
        note_types = [type_filter]
    elif isinstance(type_filter, dict) and "$in" in type_filter:
        values = type_filter["$in"]
        if isinstance(values, list) and values and all(isinstance(v, str) for v in values):
            note_types = values
    if note_types is not None:
        metadata_filters = {key: value for key, value in metadata_filters.items() if key != "type"}

    query = SearchQuery(
        # Normalized by the field validator, which maps every root spelling
        # onto None: the predicates are then the whole WHERE.
        file_path_prefix=route.path,
        metadata_filters=metadata_filters,
        note_types=note_types,
        entity_types=[SearchItemType.ENTITY],
    )
    search_client = SearchClient(client, active_project.external_id)
    response = await search_client.search(query.model_dump(), page=page, page_size=page_size)
    # Re-qualified before anything reads the rows, so the projection below
    # carries the same addresses the unprojected response does — the two arms
    # of one response shape cannot answer with different spellings.
    payload = qualify_search_paths(response.model_dump(mode="json", exclude_none=True), route)
    if not fields:
        return payload

    # Field projection hydrates from the entity's canonical Markdown,
    # because its searchable entity_metadata intentionally normalizes
    # scalar values to strings. The search hit's own `metadata` is index-row
    # metadata, not the canonical projection source. One GET per hit is unavoidable
    # (the knowledge API has no bulk entity read), so the cost that matters
    # is whether they serialize: page_size is capped at
    # MAX_DIRECTORY_PAGE_SIZE, and under per-project cloud routing that
    # would be up to 200 round trips end to end inside one find call.
    # Bounded concurrency turns the wall time into ceil(hits / limit)
    # round trips while keeping the server load predictable.
    knowledge_client = KnowledgeClient(client, active_project.external_id)
    hit_ids: list[str] = []
    for result in response.results:
        if result.external_id is None:
            raise ToolError(
                "find: search hit carries no external_id — server too old for field projection"
            )
        hit_ids.append(result.external_id)

    limiter = asyncio.Semaphore(FIELD_PROJECTION_CONCURRENCY)

    async def entity_metadata(entity_external_id: str) -> dict[str, Any] | None:
        async with limiter:
            entity = await knowledge_client.get_entity(entity_external_id)
        return projection_metadata(entity.content, entity.entity_metadata)

    # Trigger: any one projection read fails — a hit deleted between the
    #          search and its hydration, or a cloud-routed GET erroring.
    # Why: gather raises the first failure but leaves its siblings running,
    #      and the caller then unwinds out of its project client, which
    #      closes the client underneath them. Every read still queued behind
    #      the semaphore would fire against a closed client and raise into a
    #      task nobody awaits — background work outliving the resource that
    #      owns it, and a log full of secondary errors hiding the real one.
    # Outcome: the siblings are cancelled and drained inside the client's
    #          lifetime; the first failure is still what reaches the caller.
    #          On success the cancels are no-ops on already-finished tasks.
    reads = [asyncio.create_task(entity_metadata(hit_id)) for hit_id in hit_ids]
    try:
        hydrated = await asyncio.gather(*reads)
    finally:
        for read in reads:
            read.cancel()
        await asyncio.gather(*reads, return_exceptions=True)
    payload["results"] = [
        projected_row(row, metadata, fields)
        for row, metadata in zip(payload["results"], hydrated, strict=True)
    ]
    return payload
