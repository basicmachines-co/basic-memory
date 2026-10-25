"""bm_search: ranked search over notes, observations and relations.

The query pipeline is search_notes's (`_build_search_query`, the tag: shorthand,
the default search mode), plus three scopes and views:

- `lines`: matching-line windows, built by scanning each ranked hit's body for the
  query's terms, in any search mode;
- `path`: a folder subtree;
- `identifier`: one note, which is grep on a single file.

Every failure is a tool error.
"""

import re
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, BeforeValidator, Field

from basic_memory.config import ConfigManager
from basic_memory.markdown.line_scanning import LiteralLineScan, scan_lines
from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.mcp.index_readiness import project_index_required
from basic_memory.mcp.note_targets import (
    ProjectTarget,
    open_project_target,
    resolve_note,
)
from basic_memory.mcp.project_context_identifiers import (
    canonical_memory_path_for_active_route,
    split_project_permalink_prefix,
)
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import (
    Pagination,
    Filters,
    Items,
    OptionalSince,
    OutputFormat,
    Page,
    PageSize,
    ProjectParam,
    search_item_types,
)
from basic_memory.mcp.workspace_project_index import get_cached_active_workspace
from basic_memory.mcp.tools.search import (
    MultiProjectFailed,
    MultiProjectNoCriteria,
    MultiProjectNotIndexed,
    MultiProjectRan,
    _build_search_query,
    _compact_search_response,
    _search_all_projects,
    _search_type_for,
    split_tag_shorthand,
)
from basic_memory.schemas.base import normalize_note_type
from basic_memory.schemas.memory import memory_url_path
from basic_memory.schemas.search import (
    MAX_SCOPED_SEARCH_PAGE_SIZE,
    SearchResponse,
    SearchResult,
)
from basic_memory.schemas.v2.entity import EntityResolveResponse
from basic_memory.temporal import TemporalQualifierError, parse_temporal_filter
from basic_memory.utils import generate_permalink, parse_str_list

type SearchMode = Literal["hybrid", "text", "semantic", "title", "permalink"]


def _parse_mode(value: Any) -> Any:
    """Read "vector", search_notes' other name for semantic search, as "semantic"."""
    return "semantic" if value == "vector" else value


StringList = Annotated[list[str] | None, BeforeValidator(parse_str_list)]


DEFAULT_MAX_MATCHES = 10


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
    ] = DEFAULT_MAX_MATCHES,
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
    # The caller's arguments as sent, for the next-page call: the body rewrites
    # `query` for memory:// URLs and permalink patterns.
    call_arguments: dict[str, object] = {
        "query": query,
        "mode": mode,
        "filters": filters,
        "note_types": note_types,
        "items": items,
        "categories": categories,
        "since": since,
        "lines": lines,
        "max_matches": max_matches if lines is not None else None,
        "path": path,
        "compact": compact or None,
        "valid_at": valid_at,
        "valid_overlaps": valid_overlaps,
        "time_kind": time_kind,
        "min_similarity": min_similarity,
        "project": project,
        "projects": projects,
        "all_projects": all_projects or None,
    }
    # --- Argument combinations, before any I/O ---
    # Trigger: more than one argument says where to search.
    # Why: each names a location (one note, one folder, the place a memory:// URL
    #      names, some or all projects), two can disagree, and honoring one would
    #      silently search somewhere the caller did not ask.
    # Outcome: a refusal naming the scopes given.
    memory_query = query is not None and query.startswith("memory://")
    scopes = [
        name
        for name, given in (
            ("`identifier`", identifier is not None),
            ("`path`", path is not None),
            ("a memory:// query", memory_query),
            ("`projects`", projects is not None),
            ("`all_projects`", all_projects),
        )
        if given
    ]
    if len(scopes) > 1:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"{' and '.join(scopes)} each say where to search; give one.",
            try_instead="one scope; a folder can go in the URL, as 'memory://research/specs/*'",
            output_format=output_format,
        )
    if projects is not None and not projects:
        raise refusal(
            "INVALID_ARGUMENTS",
            "`projects` is empty; name at least one project, or leave it out.",
            output_format=output_format,
        )
    several_projects = all_projects or projects is not None
    # Each database answers every row up to the end of the page, in one request.
    if several_projects and page * page_size > MAX_SCOPED_SEARCH_PAGE_SIZE:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"A search across projects reaches at most result {MAX_SCOPED_SEARCH_PAGE_SIZE}; "
            f"page {page} of {page_size} ends at {page * page_size}.",
            try_instead="narrow the query, or search one project with `project`",
            output_format=output_format,
        )
    if several_projects and lines is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "`lines` matches lines in one project, not several.",
            output_format=output_format,
        )
    if several_projects and project is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "`project` names one project; `projects` and `all_projects` name several. Give one.",
            output_format=output_format,
        )
    # max_matches bounds a line scan; ranked results have no matching lines to bound.
    if max_matches != DEFAULT_MAX_MATCHES and lines is None and identifier is None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "`max_matches` bounds matching lines; it needs `lines` or `identifier`.",
            try_instead="add `lines`, or drop `max_matches`",
            output_format=output_format,
        )
    # Matching lines are note content, which `compact` exists to leave out.
    if lines is not None and compact:
        raise refusal(
            "INVALID_ARGUMENTS",
            "`lines` returns note lines and `compact` omits note content; give one.",
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
        # Trigger: a one-note search also names filters or a ranking mode.
        # Why: one note is scanned for lines, not searched; silently dropping a
        #      filter would turn an AND-filtered request into an unfiltered grep.
        # Outcome: the arguments that cannot apply are refused by name.
        ignored = [
            name
            for name, value in (
                ("mode", mode),
                ("filters", filters),
                ("note_types", note_types),
                ("items", items),
                ("categories", categories),
                ("since", since),
                ("valid_at", valid_at),
                ("valid_overlaps", valid_overlaps),
                ("time_kind", time_kind),
                ("min_similarity", min_similarity),
                ("page", page if page != 1 else None),
                ("page_size", page_size if page_size != 10 else None),
            )
            if value is not None
        ] + (["compact"] if compact else [])
        if ignored:
            raise refusal(
                "INVALID_ARGUMENTS",
                f"`identifier` searches one note's lines; {', '.join(ignored)} cannot apply to it.",
                try_instead="drop them, or search a folder with `path` instead",
                output_format=output_format,
            )
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
    if several_projects:
        searched = await _search_all_projects(
            query=query,
            page=page,
            page_size=page_size,
            search_type=mode,
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
            # bm_search re-qualifies the rows and renders text itself, so it always
            # asks for the rows.
            output_format="json",
        )
        match searched:
            case MultiProjectRan(rendered=dict() as rows):
                # Each hit carries its project, so its path can be routed back to it.
                payload = {
                    **rows,
                    "results": [
                        {
                            **row,
                            "file_path": f"{generate_permalink(row['project'])}/{row['file_path']}",
                        }
                        for row in rows["results"]
                    ],
                }
                if output_format == "json":
                    return payload
                scope = f"projects {', '.join(projects)}" if projects else "all projects"
                next_call = Pagination.of(payload).next_call("bm_search", call_arguments)
                return _format_results(payload, query, scope, next_call=next_call)
            case MultiProjectRan(rendered=str() as text):  # pragma: no cover
                # Only a text request renders text, and this call asks for JSON.
                return text
            case MultiProjectNotIndexed(guidance=guidance):
                return (
                    {"results": [], "guidance": guidance} if output_format == "json" else guidance
                )
            case MultiProjectFailed(reason=reason):
                raise refusal(
                    "UNAVAILABLE",
                    f"The search did not run in any project: {reason}",
                    try_instead="retry, or search one project with `project`",
                    output_format=output_format,
                )
            case MultiProjectNoCriteria():
                raise _nothing_to_search(output_format)

    # --- One project ---
    # Trigger: the query is a memory:// URL (no other scope, as checked above).
    # Why: the URL may name its project ('memory://research/foo'), which must route
    #      the search the way it routes a read, not fall to the active project.
    # Outcome: the URL selects the route while retaining its permalink qualification.
    routed = query if memory_query and query is not None else path or ""
    kind = "identifier" if memory_query else "path"
    async with open_project_target(
        routed, project, context, kind=kind, output_format=output_format
    ) as target:
        search_mode: str = mode or _search_type_for(None, query, [target.project.name])
        include_project = ConfigManager().config.permalinks_include_project
        if memory_query and query is not None:
            # A routed URL can name a legacy project-qualified stored permalink.
            # Preserve that identity rather than adding the current workspace prefix.
            query = memory_url_path(query if include_project else target.route.path)
            if include_project and target.route.stripped:
                # Normalize only the recognized prefix, leaving note patterns intact.
                # Both legacy project and workspace/project forms can be stored;
                # selecting a workspace must not rewrite one form into the other.
                prefixes = [target.project.permalink]
                if target.route.project is not None:
                    prefixes.append(generate_permalink(target.route.project))
                claimed = split_project_permalink_prefix(query, prefixes)
                if claimed is not None:
                    prefix, remainder = claimed
                    query = f"{prefix}/{remainder}" if remainder else prefix
            search_mode = "permalink"
        # Trigger: a permalink search ('core/*' or an exact permalink).
        # Why: permalinks are stored in index form, with the project prefix when
        #      permalinks_include_project is on (and the workspace form in Cloud), and
        #      a project-relative pattern matches none of them.
        # Outcome: the permalink is put into index form before it is matched.
        if (
            search_mode == "permalink"
            and query is not None
            and not (memory_query and target.route.stripped)
        ):
            query = canonical_memory_path_for_active_route(
                target.project,
                query,
                include_project=include_project,
                cached_workspace=await get_cached_active_workspace(context),
            )

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
            # A memory:// target is the note itself, not a folder to scope by.
            file_path_prefix=None if memory_query else target.route.path or None,
        )
        if search_query is None:
            raise _nothing_to_search(output_format)

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

    payload = target.paths.search(payload)
    if output_format == "json":
        return payload
    next_call = Pagination.of(payload).next_call("bm_search", call_arguments)
    return _format_results(payload, query, f"project '{target.project.name}'", next_call=next_call)


def _nothing_to_search(output_format: OutputFormat) -> ToolError:
    return refusal(
        "INVALID_ARGUMENTS",
        "Nothing to search for.",
        try_instead="give a `query`, or at least one of `filters`, `note_types`, "
        "`items`, `categories` or `since`",
        output_format=output_format,
    )


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
    """Replace each hit's excerpt with the matching lines of its accepted Markdown.

    Line numbers count the whole document, frontmatter included, as bm_read's do.
    Only notes with accepted Markdown and matching lines are returned.
    """
    note_hits = [hit for hit in response.results if hit.external_id is not None]
    contents = await KnowledgeClient(
        target.client, target.project.external_id
    ).accepted_note_contents([hit.external_id for hit in note_hits if hit.external_id])
    payload = response.model_dump(mode="json", exclude_none=True)
    rows: list[dict[str, Any]] = []
    for hit in note_hits:
        if hit.external_id not in contents:
            continue
        scan = scan_lines(
            contents[hit.external_id],
            terms,
            context_lines=context_lines,
            max_matches=max_matches,
        )
        if scan.match_count > 0:
            rows.append(_line_row(hit, scan))
    payload["results"] = rows
    # Trigger: line matching can drop ranked rows from this page.
    # Why: `total` counts ranked candidates, not rows with a matching line, so it
    #      cannot be used as an exact result count once rows are filtered.
    # Outcome: JSON callers page with `has_more` instead of treating `total` as exact.
    payload["total_is_exact"] = False
    return payload


def _line_row(note: SearchResult | EntityResolveResponse, scan: LiteralLineScan) -> dict[str, Any]:
    """A note's identity plus its matching-line windows."""
    return {
        "title": note.title,
        "permalink": note.permalink,
        "file_path": note.file_path,
        "external_id": note.external_id,
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
        identifier, project, context, kind="identifier", output_format=output_format
    ) as target:
        note = await resolve_note(target, identifier, output_format=output_format)
        contents = await KnowledgeClient(
            target.client, target.project.external_id
        ).accepted_note_contents([note.external_id])
    if note.external_id not in contents:
        raise refusal(
            "UNSUPPORTED",
            f"'{note.file_path}' is not a Markdown note, so it has no lines to search.",
            try_instead=f"bm_read(identifier='{note.file_path}')",
            output_format=output_format,
        )
    scan = scan_lines(
        contents[note.external_id],
        _query_terms(query),
        context_lines=context_lines,
        max_matches=max_matches,
    )
    payload = target.paths.search({"results": [_line_row(note, scan)]})
    if output_format == "json":
        return payload["results"][0]
    # One note is one result: there is no next page.
    return _format_results(payload, query, f"project '{target.project.name}'", next_call=None)


# --- Text output ---


def _format_results(
    payload: dict[str, Any], query: str | None, scope: str, *, next_call: str | None
) -> str:
    """Render hits as a compact list: path, title, score, then excerpt or matching lines.

    `next_call` is the exact call for the next page (Pagination.next_call), or None.
    """
    results = payload["results"]
    heading = f"Results for '{query}'" if query else "Results"
    continuation = (
        f"\n\nMore results available. Call {next_call} to continue."
        if next_call is not None
        else ""
    )
    if not results:
        page_hint = " on this page" if next_call is not None else ""
        return (
            f"No results for '{query or ''}'{page_hint} in {scope}."
            + (f"\n\n{payload['query_hint']}" if payload.get("query_hint") else "")
            + continuation
        )

    lines = [f"{heading} in {scope}:", ""]
    for row in results:
        header = f"- {row['file_path']} | {row['title']}"
        if row.get("score") is not None:
            header += f" | score {row['score']:.3f}"
        lines.append(header)
        if "windows" in row:
            if row["match_count"] == 0:
                lines.append("    No matching lines.")
            for window in row["windows"]:
                for number, text in enumerate(
                    window["content"].split("\n"), start=window["start_line"]
                ):
                    lines.append(f"    {number}: {text}")
            if row.get("next_match_line"):
                lines.append(f"    (more matches from line {row['next_match_line']})")
        elif row.get("matched_chunk"):
            lines.append(f"    {row['matched_chunk'][:200]}")
    return "\n".join(lines) + continuation
