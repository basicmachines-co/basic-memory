"""Shared query pipeline for bm_search.

Maps search arguments onto one ``SearchQuery``, picks the default search mode, and
runs a search across several projects as one scoped query per database.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from httpx import HTTPStatusError
from loguru import logger
from fastmcp import Context

from basic_memory.config import ConfigManager
from basic_memory.utils import (
    build_canonical_permalink,
    generate_permalink,
)
from basic_memory.mcp.async_client import get_client, routes_off_host
from basic_memory.mcp.container import get_container
from basic_memory.mcp.index_readiness import project_index_required
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.schemas.search import (
    normalize_file_path_prefix,
    SearchItemType,
    SearchQuery,
    SearchResponse,
    SearchResult,
    SearchRetrievalMode,
)
from basic_memory.repository.semantic_runtime import semantic_runtime_available

# Alias common column/model names to their frontmatter key equivalents. Users often
# pass "note_type" (the entity model column) when the frontmatter field is "type".
_METADATA_KEY_ALIASES = {"note_type": "type"}
_VALID_SEARCH_TYPES = ("hybrid", "permalink", "semantic", "text", "title", "vector")


def split_tag_shorthand(query: str | None) -> tuple[str | None, list[str] | None]:
    """Move `tag:<value>` tokens out of the query and into a tags filter.

    Handles "tag:security", "tag:coffee tag:brewing", "tag:coffee AND tag:brewing" and
    comma lists ("tag:coffee,brewing"). Done at the tool level so it works in every
    search mode: hybrid and vector need non-empty text, but the service-layer tag:
    parser clears the text after the mode is set.
    """
    if not query or "tag:" not in query.lower():
        return query, None
    raw_values = re.findall(r"tag:(\S+)", query, flags=re.IGNORECASE)
    tag_values = [value for raw in raw_values for value in raw.split(",") if value]
    if not tag_values:
        return query, None
    tags = list(dict.fromkeys(tag_values))
    # Remove tag: tokens and Boolean connectors; whatever text remains is the query.
    remainder = re.sub(r"tag:\S+", "", query, flags=re.IGNORECASE)
    remainder = re.sub(r"\b(AND|OR|NOT)\b", "", remainder).strip()
    return remainder or None, tags


def _build_search_query(
    *,
    query: str | None,
    search_type: str,
    note_types: list[str],
    entity_types: list[str],
    categories: list[str],
    after_date: str | None,
    metadata_filters: dict[str, Any] | None,
    tags: list[str] | None,
    min_similarity: float | None,
    valid_at: str | None,
    valid_overlaps: str | None,
    time_kind: str | None,
    file_path_prefix: str | None = None,
) -> SearchQuery | None:
    """Map tool parameters onto one ``SearchQuery``; ``None`` when nothing narrows the search.

    Shared by the project search and the all-projects search so both ask the API
    the same question for the same parameters.
    """
    search_query = SearchQuery()

    # Only map search_type to query fields when there is an actual query string.
    # When query is None/empty, skip the search mode block: filters-only path.
    effective_query = (query or "").strip()
    if effective_query:
        if search_type == "text":
            search_query.text = effective_query
            search_query.retrieval_mode = SearchRetrievalMode.FTS
        elif search_type in ("vector", "semantic"):
            search_query.text = effective_query
            search_query.retrieval_mode = SearchRetrievalMode.VECTOR
        elif search_type == "hybrid":
            search_query.text = effective_query
            search_query.retrieval_mode = SearchRetrievalMode.HYBRID
        elif search_type == "title":
            search_query.title = effective_query
        elif search_type == "permalink" and "*" in effective_query:
            search_query.permalink_match = effective_query
        elif search_type == "permalink":
            search_query.permalink = effective_query
        else:
            raise ValueError(
                f"Invalid search_type '{search_type}'. "
                f"Valid options: {', '.join(_VALID_SEARCH_TYPES)}"
            )

    # Add optional filters if provided (empty lists are treated as no filter)
    if entity_types:
        search_query.entity_types = [SearchItemType(t) for t in entity_types]
    if categories:
        search_query.categories = categories
    if note_types:
        search_query.note_types = note_types
    if after_date:
        search_query.after_date = after_date
    if metadata_filters:
        search_query.metadata_filters = {
            _METADATA_KEY_ALIASES.get(key, key): value for key, value in metadata_filters.items()
        }
    if tags:
        search_query.tags = tags
    if min_similarity is not None:
        search_query.min_similarity = min_similarity
    # Presence, not truthiness, for the same reason as everywhere else on this path:
    # these are assigned after construction, so the model's own blank guard never
    # runs here, and a blank has already been refused by the tool.
    if valid_at is not None:
        search_query.valid_at = valid_at
    if valid_overlaps is not None:
        search_query.valid_overlaps = valid_overlaps
    if time_kind is not None:
        search_query.time_kind = time_kind
    # Assigned after construction, so the field validator does not run: normalize here.
    if file_path_prefix is not None:
        search_query.file_path_prefix = normalize_file_path_prefix(file_path_prefix)

    if search_query.no_criteria():
        return None

    # Default to entity-level results to avoid returning individual
    # observations/relations as separate search results (see issue #31).
    # Applied after no_criteria() so that the implicit default doesn't
    # mask a truly empty search request.
    if not search_query.entity_types:
        # Trigger: a category or valid-time filter was supplied without an
        #          explicit entity_types.
        # Why: both only exist on observations. Categories live on observation
        #      rows, and temporal assertions are projected against an
        #      observation's (type, id). Defaulting to "entity" would AND either
        #      filter against entity rows and return nothing, defeating the
        #      whole query.
        # Outcome: scope the implicit default to observations so
        #          bm_search(categories=[...]) and bm_search(valid_at=...)
        #          return the matching bullets.
        if search_query.categories or search_query.has_temporal_filter():
            search_query.entity_types = [SearchItemType("observation")]
        else:
            search_query.entity_types = [SearchItemType("entity")]
    return search_query


def _compact_search_response(response: SearchResponse) -> SearchResponse:
    """Keep discovery identities and pagination without repeating source prose."""
    return response.model_copy(
        update={
            "results": [
                result.model_copy(
                    update={
                        "content": None,
                        "matched_chunk": None,
                        "content_length": None,
                        "content_truncated": None,
                        # Observation labels embed excerpts too. Keep owner-file
                        # navigation and IDs without duplicating source prose.
                        "title": (result.category or "observation")
                        if result.type == SearchItemType.OBSERVATION
                        else result.title,
                        "permalink": result.file_path
                        if result.type == SearchItemType.OBSERVATION
                        else result.permalink,
                    }
                )
                for result in response.results
            ]
        }
    )


# Uppercase AND/OR/NOT standing alone (or beside parentheses) is full-text query syntax.
_BOOLEAN_OPERATOR = re.compile(r"(?<![^\s()])(?:AND|OR|NOT)(?![^\s()])")


def _search_type_for(
    search_type: str | None, query: str | None, project_names: Sequence[str]
) -> str:
    """The search type to run: the caller's choice, else a default suited to the query.

    Trigger: no explicit search_type and the query uses Boolean operators.
    Why: the vector half of hybrid search has no notion of NOT or AND, so it adds back
         notes the Boolean expression excludes ("coffee NOT pour" returned the note
         about pour-over).
    Outcome: such queries run as full-text search; others use the configured default.
    """
    if search_type:
        return search_type
    if query and _BOOLEAN_OPERATOR.search(query):
        return "text"
    return _default_search_type(project_names)


def _default_search_type(project_names: Sequence[str]) -> str:
    """Pick default search mode from config, falling back to what the target can run.

    Priority: config default_search_type > hybrid where vector search can run > text.
    """
    try:
        config = get_container().config
    except RuntimeError:
        config = ConfigManager().config

    if config.default_search_type:
        return config.default_search_type

    # A host that cannot load sqlite-vec runs keyword-only (#711); defaulting to
    # hybrid there would turn every plain search into a semantic-unavailable error.
    # Searches that leave this process (Cloud, or an injected client factory) run on
    # Cloud's Postgres, which always has vectors, so the local runtime does not
    # limit them.
    remote = routes_off_host(config, project_names)
    return "hybrid" if remote or semantic_runtime_available(config) else "text"


def _is_service_unavailable_error(error: BaseException) -> bool:
    """Return whether an explicit HTTP cause marks a retryable service outage."""
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, HTTPStatusError):
            return current.response.status_code == 503
        current = current.__cause__
    return False


def _valid_project_id(value: object) -> str | None:
    """Return a UUID project id string when one is present."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _names_project(token: str, *spellings: object) -> bool:
    """Return True when a caller's project token names one of a project's spellings.

    Project routing matches names by permalink (#1388), so `ALPHA` and `alpha`
    are the same project there. Search scoping compared exact strings, which made
    a case-mismatched BASIC_MEMORY_MCP_PROJECT or `projects=["BETA"]` see no
    projects at all (#1630).
    """
    token_permalink = generate_permalink(token.strip())
    return any(
        isinstance(spelling, str) and generate_permalink(spelling) == token_permalink
        for spelling in spellings
    )


def _matches_constrained_project(project: dict[str, Any], constrained_project: object) -> bool:
    """Return True when a project list row satisfies BASIC_MEMORY_MCP_PROJECT."""
    if not isinstance(constrained_project, str) or not constrained_project.strip():
        return True

    return _names_project(
        constrained_project,
        project.get("name"),
        project.get("qualified_name"),
        project.get("external_id"),
    )


@dataclass(frozen=True)
class SearchProjectRef:
    """One project an all-projects search may read, and the database it lives in.

    ``name`` is the routable spelling (``workspace/project`` for a cloud project),
    which is also the prefix results are qualified with. ``workspace_tenant_id`` is
    ``None`` for the local database; every cloud workspace is its own database.
    """

    name: str
    external_id: str
    id: int
    workspace_tenant_id: str | None
    path: str

    @property
    def bare_name(self) -> str:
        return self.name.rsplit("/", 1)[-1]


def _search_project_refs(projects_payload: object) -> list[SearchProjectRef]:
    """Extract the projects an all-projects search can read from the project list."""
    if not isinstance(projects_payload, dict):
        return []

    payload = cast(dict[str, Any], projects_payload)
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return []

    refs: list[SearchProjectRef] = []
    seen: set[str] = set()
    constrained_project = payload.get("constrained_project")
    for item in projects:
        if not isinstance(item, dict) or not _matches_constrained_project(
            item, constrained_project
        ):
            continue

        project = item.get("qualified_name") or item.get("name")
        external_id = _valid_project_id(item.get("external_id"))
        internal_id = item.get("id")
        # A scoped search addresses a project by its id and attributes hits by its
        # external id; a list row missing either cannot be searched this way.
        if (
            not isinstance(project, str)
            or not project.strip()
            or external_id is None
            or not isinstance(internal_id, int)
            or isinstance(internal_id, bool)
        ):
            continue
        if external_id in seen:
            continue
        seen.add(external_id)
        tenant = item.get("workspace_tenant_id")
        refs.append(
            SearchProjectRef(
                name=project,
                external_id=external_id,
                id=internal_id,
                workspace_tenant_id=tenant if isinstance(tenant, str) and tenant else None,
                path=str(item.get("path") or ""),
            )
        )
    return refs


def _select_project_refs(
    refs: list[SearchProjectRef], projects: list[str]
) -> list[SearchProjectRef]:
    """Keep the projects a caller named, by name, workspace-qualified name, or external id."""
    selected: list[SearchProjectRef] = []
    unknown: list[str] = []
    for requested in projects:
        token = requested.strip()
        matches = [
            ref for ref in refs if _names_project(token, ref.name, ref.bare_name, ref.external_id)
        ]
        if not matches:
            unknown.append(token)
            continue
        for ref in matches:
            if ref not in selected:
                selected.append(ref)
    if unknown:
        available = ", ".join(sorted(ref.name for ref in refs)) or "none"
        raise ValueError(
            f"Unknown project(s): {', '.join(unknown)}. Available projects: {available}"
        )
    return selected


async def _load_search_project_refs(context: Context | None = None) -> list[SearchProjectRef]:
    """Load the projects a search across projects may read."""
    from basic_memory.mcp.tools.project_management import list_memory_projects

    return _search_project_refs(await list_memory_projects(output_format="json", context=context))


def _result_score(result: SearchResult | dict[str, Any]) -> float:
    """Return the strength of one hit, comparable across databases and modes.

    Vector similarity and fused hybrid scores are positive with higher meaning
    better. Full-text scores are not: SQLite bm25 is negative with lower meaning
    better, Postgres ts_rank positive with higher meaning better. Every backend
    orders its own page by strength, and the magnitude is that strength in all of
    them, so the merge ranks by magnitude and a stable sort keeps each database's
    own order among equals.
    """
    score = result.score if isinstance(result, SearchResult) else result.get("score")
    return abs(float(score)) if isinstance(score, int | float) else 0.0


def _qualify_permalink_for_project(permalink: object, project: str | None) -> object:
    """Return a workspace-qualified permalink when the project ref supplies one."""
    if not isinstance(permalink, str) or not permalink.strip():
        return permalink
    if not isinstance(project, str) or "/" not in project.strip("/"):
        return permalink

    normalized_permalink = permalink.strip("/")
    qualified_project = project.strip("/")
    if normalized_permalink == qualified_project or normalized_permalink.startswith(
        f"{qualified_project}/"
    ):
        return normalized_permalink

    workspace_slug, project_permalink = qualified_project.split("/", 1)
    return build_canonical_permalink(
        project_permalink,
        normalized_permalink,
        include_project=True,
        workspace_permalink=workspace_slug,
    )


def _qualify_result_for_project(
    result: SearchResult,
    project: str,
    *,
    compact: bool = False,
) -> dict[str, Any]:
    """Attach the searched workspace/project prefix to one result's permalink."""
    result_data = result.model_dump()
    if compact and result_data.get("type") == SearchItemType.OBSERVATION:
        # This is an exact file read target, not a generated permalink:
        # retain its extension, spaces, and case under the local project or
        # workspace/project route.
        result_data["permalink"] = f"{project.strip('/')}/{result_data['file_path'].lstrip('/')}"
    else:
        result_data["permalink"] = _qualify_permalink_for_project(
            result_data.get("permalink"), project
        )
    return result_data


def _attribute_results(
    results: list[SearchResult],
    refs: list[SearchProjectRef],
    *,
    compact: bool,
) -> list[dict[str, Any]]:
    """Qualify each hit's permalink with the project the server says it came from."""
    refs_by_external_id = {ref.external_id: ref for ref in refs}
    attributed: list[dict[str, Any]] = []
    for result in results:
        ref = refs_by_external_id.get(result.project_external_id or "")
        if ref is None:
            raise ValueError(
                "The search API returned a result it did not attribute to a project in "
                "the requested scope. The server is likely older than this client; "
                "upgrade it before searching across projects."
            )
        attributed.append(_qualify_result_for_project(result, ref.name, compact=compact))
    return attributed


def _database_label(tenant_id: str | None, refs: list[SearchProjectRef]) -> str:
    """Name one searched database for logs and error responses."""
    if tenant_id is None:
        return "local projects"
    return f"workspace {refs[0].name.split('/', 1)[0]}"


def _project_item(ref: SearchProjectRef) -> ProjectItem:
    """The project shape the readiness check reads (external id and name)."""
    return ProjectItem(id=ref.id, external_id=ref.external_id, name=ref.bare_name, path=ref.path)


@dataclass(frozen=True, slots=True)
class MultiProjectRan:
    """The search ran in at least one database; `payload` is the merged page as JSON."""

    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MultiProjectNotIndexed:
    """Nothing matched, and a project has not been indexed yet, so the miss is not final."""

    guidance: str


@dataclass(frozen=True, slots=True)
class MultiProjectFailed:
    """No database answered: the search did not run."""

    reason: str


@dataclass(frozen=True, slots=True)
class MultiProjectNoCriteria:
    """The request named nothing to search for."""


type MultiProjectSearch = (
    MultiProjectRan | MultiProjectNotIndexed | MultiProjectFailed | MultiProjectNoCriteria
)


async def _search_all_projects(
    *,
    query: str | None,
    page: int,
    page_size: int,
    search_type: str | None,
    note_types: list[str],
    entity_types: list[str],
    categories: list[str],
    after_date: str | None,
    metadata_filters: dict[str, Any] | None,
    tags: list[str] | None,
    min_similarity: float | None,
    valid_at: str | None,
    valid_overlaps: str | None,
    time_kind: str | None,
    context: Context | None,
    compact: bool = False,
    projects: list[str] | None = None,
) -> MultiProjectSearch:
    """Search every accessible project, one query per database.

    Projects that share a database are searched with one scoped query, so within that
    database the answer is one ranking rather than per-project pages merged after the
    fact. The local database is one group and each cloud workspace is another; only
    the merge across databases happens in this process, by score.
    """
    requested_page = max(page, 1)
    requested_page_size = max(page_size, 1)
    # Presence, not truthiness: a blank value is refused by `parse_temporal_filter`
    # before any project is searched, so anything not None is a real question here.
    temporal_requested = valid_at is not None or valid_overlaps is not None or time_kind is not None
    project_refs = await _load_search_project_refs(context=context)
    if projects:
        project_refs = _select_project_refs(project_refs, projects)
    if not project_refs:
        response = SearchResponse(
            results=[],
            current_page=requested_page,
            page_size=requested_page_size,
            total=0,
            total_is_exact=True,
            has_more=False,
            temporal_applied=True if temporal_requested else None,
        )
        return MultiProjectRan(response.model_dump(mode="json", exclude_none=True))

    effective_search_type = _search_type_for(search_type, query, [ref.name for ref in project_refs])
    search_query = _build_search_query(
        query=query,
        search_type=effective_search_type,
        note_types=note_types,
        entity_types=entity_types,
        categories=categories,
        after_date=after_date,
        metadata_filters=metadata_filters,
        tags=tags,
        min_similarity=min_similarity,
        valid_at=valid_at,
        valid_overlaps=valid_overlaps,
        time_kind=time_kind,
    )
    if search_query is None:
        return MultiProjectNoCriteria()
    query_payload = search_query.model_dump()

    # Import here to avoid circular import (tools -> clients -> utils -> tools)
    from basic_memory.mcp.clients import ScopedSearchClient

    databases: dict[str | None, list[SearchProjectRef]] = {}
    for ref in project_refs:
        databases.setdefault(ref.workspace_tenant_id, []).append(ref)

    # Each database answers the whole prefix this page needs, so the merge across
    # databases can slice it without a second round of requests.
    per_database_page_size = requested_page * requested_page_size
    merged_results: list[dict[str, Any]] = []
    total = 0
    total_is_exact = True
    any_database_has_more = False
    # How many databases actually answered. A database that fails is skipped with a
    # warning, so without this the caller cannot tell "no note matched" from
    # "nothing ran".
    databases_answered = 0
    failures: list[str] = []
    query_hint: str | None = None

    for tenant_id, refs in databases.items():
        label = _database_label(tenant_id, refs)
        try:
            async with get_client(workspace=tenant_id) as client:
                response = await ScopedSearchClient(client).search(
                    query_payload,
                    project_ids=[ref.id for ref in refs],
                    page=1,
                    page_size=per_database_page_size,
                )
                if not response.results:
                    # An empty page is a trustworthy miss only after an index pass.
                    for ref in refs:
                        guidance = await project_index_required(client, _project_item(ref))
                        if guidance is not None:
                            return MultiProjectNotIndexed(guidance)
        except Exception as exc:
            if _is_service_unavailable_error(exc):
                return MultiProjectFailed(reason=f"{label}: {exc}")
            logger.warning(f"Multi-project search failed for {label}: {exc}")
            failures.append(f"{label}: {exc}")
            total_is_exact = False
            continue

        databases_answered += 1
        if compact:
            response = _compact_search_response(response)
        if response.query_hint:
            query_hint = response.query_hint
        total += (
            response.total
            if response.total > 0
            else len(response.results) + (1 if response.has_more else 0)
        )
        total_is_exact = total_is_exact and response.total_is_exact
        any_database_has_more = any_database_has_more or response.has_more
        merged_results.extend(_attribute_results(response.results, refs, compact=compact))

    # Trigger: a valid-time filter was requested and not one database answered.
    # Why: each database confirms the filter through the client or is refused by it,
    #   and a refusal is caught above, logged, and skipped -- so a fleet of servers
    #   predating SPEC-82 drops every database and arrives here indistinguishable from
    #   "no note matched". Claiming `temporal_applied` on that would confirm a filter
    #   that ran nowhere, which is the version skew the client's own check exists to
    #   make loud.
    # Outcome: the skew is propagated as one error naming it, rather than returning an
    #   empty result wearing the shape of a successful filtered search.
    if temporal_requested and databases_answered == 0:
        raise ValueError(
            "No project applied the requested valid-time filter: every project was "
            "skipped, so the filter ran nowhere and an empty result would not mean "
            "'no matches'. The servers are likely older than this client; upgrade them "
            "or drop valid_at / valid_overlaps / time_kind from the query."
        )
    if databases_answered == 0:
        # Every database failed. That is a failed search, not an empty one.
        return MultiProjectFailed(reason="; ".join(failures))

    # Each database owns retrieval and optional reranking behind its typed API client.
    # The MCP process only merges returned scores; it must not instantiate repository
    # providers with local credentials for content fetched through another route.
    sorted_results = sorted(merged_results, key=_result_score, reverse=True)
    start = (requested_page - 1) * requested_page_size
    end = start + requested_page_size
    paged_results = sorted_results[start:end]
    response = SearchResponse.model_validate(
        {
            "results": paged_results,
            # Only propagate query guidance when every database answered and the
            # aggregate is empty; one empty database must not label a successful search.
            "query_hint": query_hint
            if requested_page == 1 and not merged_results and databases_answered == len(databases)
            else None,
            "current_page": requested_page,
            "page_size": requested_page_size,
            "total": total,
            "total_is_exact": total_is_exact,
            "has_more": any_database_has_more or total > end or len(sorted_results) > end,
            # Confirmed only because a database answered: `databases_answered` is
            # non-zero here for any temporal query, guarded immediately above.
            "temporal_applied": True if temporal_requested else None,
        }
    )

    project_names = {ref.external_id: ref.name for ref in project_refs}
    payload = response.model_dump(mode="json", exclude_none=True)
    for item in payload["results"]:
        item["project"] = project_names[item["project_external_id"]]
    return MultiProjectRan(payload)
