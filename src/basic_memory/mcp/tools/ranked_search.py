"""bm_search: ranked search over notes, observations and relations (SPEC-35.2, #1830).

Replaces search_notes and grep. It runs the same query pipeline as search_notes
(`_build_search_query`, the same tag: shorthand, the same default search mode) and
adds three things:

- `lines`: grep's matching-line windows, built by scanning each ranked hit's body
  for the query's terms, in any search mode;
- `path`: limit the search to a folder subtree;
- `identifier`: search one note, which is grep on a single file.

Unlike search_notes, every failure is a tool error (#1813).
"""

import re
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import Context
from pydantic import AliasChoices, BeforeValidator, Field

from basic_memory.markdown.line_scanning import LiteralLineScan, scan_lines
from basic_memory.mcp.clients import KnowledgeClient, ResourceClient, SearchClient
from basic_memory.mcp.index_readiness import project_index_required
from basic_memory.mcp.note_reads import read_note_json_by_external_id
from basic_memory.mcp.note_targets import (
    ProjectTarget,
    open_project_target,
    qualify_search_paths,
    resolve_note,
)
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import (
    Filters,
    Items,
    OptionalSince,
    OutputFormat,
    Page,
    PageSize,
    ProjectParam,
    search_item_types,
)
from basic_memory.mcp.tools.search import (
    _build_search_query,
    _compact_search_response,
    _search_all_projects,
    _search_type_for,
    split_tag_shorthand,
)
from basic_memory.schemas.base import normalize_note_type
from basic_memory.schemas.memory import memory_url_path
from basic_memory.schemas.search import SearchResponse
from basic_memory.temporal import TemporalQualifierError, parse_temporal_filter
from basic_memory.utils import parse_str_list

type SearchMode = Literal["hybrid", "text", "semantic", "title", "permalink"]


def _parse_mode(value: Any) -> Any:
    """Read "vector", search_notes' other name for semantic search, as "semantic"."""
    return "semantic" if value == "vector" else value


StringList = Annotated[list[str] | None, BeforeValidator(parse_str_list)]


@mcp.tool(
    name="bm_search",
    title="Search notes",
    description=(
        "Search a Basic Memory project, ranked by relevance. `query` supports quoted "
        "phrases, AND/OR/NOT and tag:name. `mode` picks hybrid (the default when semantic "
        "search is available), text, semantic, title or permalink (globs with *). Narrow "
        "with `filters` (frontmatter), `note_types`, `items` (notes, observations, "
        "relations) and `since`. `lines` returns the matching lines of each hit with that "
        "many lines of context, like grep. `path` limits the search to a folder; "
        "`identifier` searches one note. Read full notes with bm_read."
    ),
    tags={"search"},
    annotations={
        "title": "Search notes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_search(
    query: Annotated[
        str | None,
        Field(
            validation_alias=AliasChoices("query", "q", "text", "pattern"),
            description="What to search for. Omit to search by filters alone.",
        ),
    ] = None,
    mode: Annotated[
        SearchMode | None,
        BeforeValidator(_parse_mode),
        Field(validation_alias=AliasChoices("mode", "search_type"), description="Search mode."),
    ] = None,
    filters: Filters = None,
    note_types: Annotated[
        StringList,
        Field(
            validation_alias=AliasChoices("note_types", "note_type", "types"),
            description="Frontmatter `type` values, e.g. ['person', 'meeting'].",
        ),
    ] = None,
    items: Items = None,
    categories: Annotated[
        StringList,
        Field(
            validation_alias=AliasChoices("categories", "category"),
            description="Observation categories to match exactly, e.g. ['decision'].",
        ),
    ] = None,
    since: OptionalSince = None,
    lines: Annotated[
        int | None,
        Field(
            ge=0,
            le=10,
            validation_alias=AliasChoices("lines", "context_lines"),
            description=(
                "Return each hit's matching lines with this many lines of context (0-10), "
                "instead of a match excerpt."
            ),
        ),
    ] = None,
    max_matches: Annotated[
        int, Field(ge=1, le=100, description="Matching lines shown per note with `lines`.")
    ] = 10,
    identifier: Annotated[
        str | None,
        Field(
            validation_alias=AliasChoices("identifier", "file_path"),
            description="Search only this note; returns its matching lines.",
        ),
    ] = None,
    path: Annotated[
        str | None, Field(description="Search only this folder and its subfolders.")
    ] = None,
    compact: Annotated[
        bool, Field(description="Omit note bodies and excerpts; keep identifiers and scores.")
    ] = False,
    valid_at: Annotated[
        str | None,
        Field(description="Specialist: only facts whose authored valid time contains this date."),
    ] = None,
    valid_overlaps: Annotated[
        str | None,
        Field(description="Specialist: only facts whose valid range overlaps '[start,end)'."),
    ] = None,
    time_kind: Annotated[
        str | None,
        Field(description="Specialist: effective, valid, occurred, due or mentioned."),
    ] = None,
    min_similarity: Annotated[
        float | None, Field(description="Override the semantic similarity floor (0-1).")
    ] = None,
    projects: Annotated[StringList, Field(description="Search these projects together.")] = None,
    all_projects: Annotated[
        bool,
        Field(
            validation_alias=AliasChoices("all_projects", "search_all_projects"),
            description="Search every project you can access.",
        ),
    ] = False,
    page: Page = 1,
    page_size: PageSize = 10,
    output_format: OutputFormat = "text",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Search notes, observations and relations."""
    # --- Argument combinations, before any I/O ---
    if identifier is not None and path is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "`identifier` and `path` both scope the search; give one.",
            output_format=output_format,
        )
    several_projects = all_projects or bool(projects)
    if several_projects and (identifier is not None or path is not None or lines is not None):
        raise refusal(
            "INVALID_ARGUMENTS",
            "`identifier`, `path` and `lines` search one project, not several.",
            output_format=output_format,
        )
    # The text a matching line must contain; empty means no line matching is possible.
    line_query = (query or "").strip()
    if (lines is not None or identifier is not None) and not line_query:
        raise refusal(
            "INVALID_ARGUMENTS",
            "Matching lines need a `query` to match.",
            output_format=output_format,
        )
    if (lines is not None or identifier is not None) and ("\n" in line_query or "\r" in line_query):
        raise refusal(
            "INVALID_ARGUMENTS",
            "Line matching needs a single-line query.",
            output_format=output_format,
        )
    if valid_at and valid_overlaps:
        raise refusal(
            "INVALID_ARGUMENTS",
            "Use either valid_at (containment) or valid_overlaps (overlap), not both.",
            output_format=output_format,
        )
    try:
        parse_temporal_filter(valid_at=valid_at, valid_overlaps=valid_overlaps, time_kind=time_kind)
    except TemporalQualifierError as error:
        raise refusal(
            "INVALID_ARGUMENTS", f"Invalid valid-time filter: {error}", output_format=output_format
        ) from error

    # --- One note: grep on a single file ---
    if identifier is not None:
        return await _search_one_note(
            identifier,
            line_query,
            context_lines=lines or 0,
            max_matches=max_matches,
            project=project,
            context=context,
            output_format=output_format,
        )

    # --- Shared query shape ---
    query, tags = split_tag_shorthand(query, None)
    normalized_note_types = [normalize_note_type(note_type) for note_type in note_types or []]
    entity_types = [item.value for item in search_item_types(items) or []]

    # --- Several projects ---
    if several_projects and project is None:
        return await _search_all_projects(
            query=query,
            page=page,
            page_size=page_size,
            search_type=mode,
            output_format=output_format,
            note_types=normalized_note_types,
            entity_types=entity_types,
            categories=categories or [],
            after_date=since,
            metadata_filters=filters,
            tags=tags,
            status=None,
            min_similarity=min_similarity,
            valid_at=valid_at,
            valid_overlaps=valid_overlaps,
            time_kind=time_kind,
            context=context,
            compact=compact,
            projects=projects,
        )

    # --- One project ---
    async with open_project_target(
        path or "", project, context, output_format=output_format
    ) as target:
        # A memory:// query names a note by permalink.
        search_mode: str = mode or _search_type_for(None, query, [target.project.name])
        if query is not None and query.startswith("memory://"):
            query, search_mode = memory_url_path(query), "permalink"

        search_query = _build_search_query(
            query=query,
            search_type=search_mode,
            note_types=normalized_note_types,
            entity_types=entity_types,
            categories=categories or [],
            after_date=since,
            metadata_filters=filters,
            tags=tags,
            status=None,
            min_similarity=min_similarity,
            valid_at=valid_at,
            valid_overlaps=valid_overlaps,
            time_kind=time_kind,
            file_path_prefix=target.route.path or None,
        )
        if search_query is None:
            raise refusal(
                "INVALID_ARGUMENTS",
                "Nothing to search for.",
                try_instead="give a `query`, or at least one of `filters`, `note_types`, "
                "`items`, `categories` or `since`",
                output_format=output_format,
            )

        response = await SearchClient(target.client, target.project.external_id).search(
            search_query.model_dump(), page=page, page_size=page_size
        )
        # An empty page is a trustworthy miss only after the project has been indexed.
        if not response.results:
            guidance = await project_index_required(target.client, target.project)
            if guidance is not None:
                return (
                    {"results": [], "guidance": guidance} if output_format == "json" else guidance
                )

        if lines is not None:
            payload = await _with_matching_lines(
                target, response, _query_terms(line_query), lines, max_matches
            )
        else:
            if compact:
                response = _compact_search_response(response)
            payload = response.model_dump(mode="json", exclude_none=True)

    payload = qualify_search_paths(payload, target.route)
    if output_format == "json":
        return payload
    return _format_results(payload, query, target.project.name)


# --- Line matching ---


def _query_terms(query: str) -> list[str]:
    """The words and quoted phrases a matching line should contain.

    Boolean operators, parentheses, a leading '-' and tag: tokens are query syntax,
    not text, so they are dropped.
    """
    phrases = re.findall(r'"([^"]+)"', query)
    words = re.sub(r'"[^"]*"', " ", query).split()
    terms = [*phrases]
    for word in words:
        word = word.strip("()").lstrip("-")
        if (
            word
            and word.upper() not in {"AND", "OR", "NOT"}
            and not word.lower().startswith("tag:")
        ):
            terms.append(word)
    return terms or [query.strip()]


async def _with_matching_lines(
    target: ProjectTarget,
    response: SearchResponse,
    terms: list[str],
    context_lines: int,
    max_matches: int,
) -> dict[str, Any]:
    """Replace each hit's excerpt with the matching lines of its accepted content."""
    knowledge_client = KnowledgeClient(target.client, target.project.external_id)
    resource_client = ResourceClient(target.client, target.project.external_id)
    payload = response.model_dump(mode="json", exclude_none=True)
    rows: list[dict[str, Any]] = []
    for hit in response.results:
        # Observations and relations are lines of their note; a hit without a note
        # id has no body to scan.
        if hit.external_id is None:
            continue
        note = await read_note_json_by_external_id(
            knowledge_client=knowledge_client,
            resource_client=resource_client,
            entity_external_id=hit.external_id,
            include_frontmatter=True,
        )
        scan = scan_lines(
            note["content"], terms, context_lines=context_lines, max_matches=max_matches
        )
        rows.append(_line_row(note, hit.external_id, scan))
    payload["results"] = rows
    return payload


def _line_row(note: Any, external_id: str, scan: LiteralLineScan) -> dict[str, Any]:
    return {
        "title": note["title"],
        "permalink": note["permalink"],
        "file_path": note["file_path"],
        "external_id": external_id,
        **asdict(scan),
    }


async def _search_one_note(
    identifier: str,
    query: str,
    *,
    context_lines: int,
    max_matches: int,
    project: str | None,
    context: Context | None,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    """grep on one note: its matching lines, with context."""
    async with open_project_target(
        identifier, project, context, output_format=output_format
    ) as target:
        note_ref = await resolve_note(target, identifier, output_format=output_format)
        note = await read_note_json_by_external_id(
            knowledge_client=KnowledgeClient(target.client, target.project.external_id),
            resource_client=ResourceClient(target.client, target.project.external_id),
            entity_external_id=note_ref.external_id,
            include_frontmatter=True,
        )
    scan = scan_lines(
        note["content"], _query_terms(query), context_lines=context_lines, max_matches=max_matches
    )
    payload = qualify_search_paths(
        {"results": [_line_row(note, note_ref.external_id, scan)]}, target.route
    )
    if output_format == "json":
        return payload["results"][0]
    return _format_results(payload, query, target.project.name)


# --- Text output ---


def _format_results(payload: dict[str, Any], query: str | None, project_name: str) -> str:
    """Render hits as a compact list: path, title, score, then excerpt or matching lines."""
    results = payload["results"]
    heading = f"Results for '{query}'" if query else "Results"
    if not results:
        return f"No results for '{query or ''}' in project '{project_name}'." + (
            f"\n\n{payload['query_hint']}" if payload.get("query_hint") else ""
        )

    lines = [f"{heading} in project '{project_name}':", ""]
    for row in results:
        header = f"- {row['file_path']} | {row['title']}"
        if row.get("score") is not None:
            header += f" | score {row['score']:.3f}"
        lines.append(header)
        if "windows" in row:
            for window in row["windows"]:
                for number, text in enumerate(
                    window["content"].split("\n"), start=window["start_line"]
                ):
                    lines.append(f"    {number}: {text}")
            if row.get("next_match_line"):
                lines.append(f"    (more matches from line {row['next_match_line']})")
        elif row.get("matched_chunk"):
            lines.append(f"    {row['matched_chunk'][:200]}")
    if payload.get("has_more"):
        lines.extend(["", "More results: call again with page + 1."])
    return "\n".join(lines)
