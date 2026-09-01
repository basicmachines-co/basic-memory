"""POSIX-style read-only tools for Basic Memory MCP server.

Six familiar Unix verbs — cat, grep, ls, find, tail, man — each a thin
translation over the same typed API clients the canonical tools use. They are
tagged ``POSIX_TOOLS_TAG`` and hidden by default: the composition root in
``basic_memory.mcp.server`` flips their visibility from the
``enable_posix_tools`` config flag at lifespan startup, so no tool body ever
checks config itself.

Projects are mount points (#1415): when no ``project``/``project_id`` param is
given, a path or identifier whose first segment names an addressable project
routes there, with the remainder as the project-relative path — inputs accept
exactly the '<project>/path' identifiers tool outputs produce. An explicit
project param plus an agreeing prefix strips the prefix; a disagreeing one
refuses naming both. Where more than one project is addressable — several local
projects, or a cloud workspace holding several — an unrecognized first segment
refuses with the project list rather than silently defaulting (#1421); the
mount view and the resolver read one list, so anything ``ls "/"`` advertises is
addressable by name. The resolver answers with both routing fields — the project
and its external_id — and every tool hands that pair to ``get_project_client``
verbatim, which is what keeps a cloud mount bound to the workspace whose listing
advertised it.

Collision rule: the project always wins over a same-named top-level folder in
the default project, so that folder is only reachable unqualified when a single
project is addressable (where there is no ambiguity); the qualified
'<project>/folder/...' form always reaches it. ``man`` is excluded — its
``project`` param names the manual project, not a data project.
"""

import asyncio
import json
import math
import os
import re
from typing import Annotated, Any, Optional

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import BeforeValidator

from basic_memory.config import ConfigManager
from basic_memory.man import bundled_pages, find_page, parse_page_ref, render_index
from basic_memory.mcp.container import get_container
from basic_memory.mcp.note_reads import read_note_json_by_external_id
from basic_memory.mcp.project_context import (
    ProjectPathRoute,
    addressable_projects,
    get_project_client,
    resolve_project_path_route,
)
from basic_memory.mcp.server import POSIX_TOOLS_TAG, mcp, set_posix_tools_visibility
from basic_memory.repository.metadata_filters import MetadataPath, parse_metadata_path
from basic_memory.schemas.directory import (
    DEFAULT_DIRECTORY_PAGE_SIZE,
    MAX_DIRECTORY_PAGE_SIZE,
    DirectoryListResponse,
    DirectoryNode,
)
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode
from basic_memory.utils import coerce_list, generate_permalink

# --- Round-trip coherence ---
# A path a routed verb returns must be a path the resolver accepts. When a call
# addresses its project in the path ('ls research/notes'), the project prefix is
# stripped before the project-scoped API sees it, so that API answers in
# project-relative paths — '/notes'. Handing those back unchanged breaks the
# navigation loop the mount model promises: feeding '/notes' into `ls` refuses as
# unqualified, or worse, opens a *different* project that happens to be mounted
# as 'notes'.
#
# One rule decides *whether* and *with what* (_route_prefix); each response
# schema says *where*. That split is deliberate. Rewriting by key name anywhere
# in the payload also rewrote a note's own frontmatter when the author happened
# to use a `file_path:` key, so a routed `cat` returned frontmatter that
# disagreed with both its own `content` and the stored file. Transport metadata
# and note content are different things that can spell a key the same way, and
# only position tells them apart.


def _route_prefix(route: ProjectPathRoute) -> str | None:
    """The prefix a routed response must re-attach, or None if nothing was stripped.

    None means the caller addressed the project some other way (an explicit
    param, or a single-project session), so the project-relative paths it gets
    back are already the ones it can feed back.
    """
    if not route.stripped or route.project is None:
        return None
    # The permalink form is the spelling `ls "/"` advertises and the resolver
    # normalizes to, so it round-trips for display names too ('My Research').
    return generate_permalink(route.project)


def _requalified_path(value: str, prefix: str) -> str:
    """Re-attach a stripped project prefix, preserving the field's slash shape."""
    if value.startswith("/"):
        return f"/{prefix}" if value == "/" else f"/{prefix}{value}"
    return f"{prefix}/{value}" if value else prefix


def qualify_note_paths(payload: dict[str, Any], route: ProjectPathRoute) -> dict[str, Any]:
    """Re-qualify a note payload's transport path.

    ``file_path`` is the note's address and is re-qualified. ``frontmatter`` is
    the note's own YAML — canonical content that must come back byte for byte,
    even when it carries keys named like transport fields — and ``permalink`` is
    an identity with its own canonical form; neither is touched.
    """
    prefix = _route_prefix(route)
    if prefix is None:
        return payload
    return {**payload, "file_path": _requalified_path(payload["file_path"], prefix)}


def _requalified_directory_node(node: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Re-qualify one DirectoryNode's addressing fields, and its children."""
    requalified = dict(node)
    directory_path = node.get("directory_path")
    if isinstance(directory_path, str):
        requalified["directory_path"] = _requalified_path(directory_path, prefix)
    # file_path is Optional on DirectoryNode: directory rows carry no file.
    file_path = node.get("file_path")
    if isinstance(file_path, str):
        requalified["file_path"] = _requalified_path(file_path, prefix)
    children = node.get("children")
    if children:
        requalified["children"] = [_requalified_directory_node(child, prefix) for child in children]
    return requalified


def qualify_listing_paths(payload: dict[str, Any], route: ProjectPathRoute) -> dict[str, Any]:
    """Re-qualify a directory listing's node addressing fields."""
    prefix = _route_prefix(route)
    if prefix is None:
        return payload
    return {
        **payload,
        "nodes": [_requalified_directory_node(node, prefix) for node in payload["nodes"]],
    }


# The manual project holds the non-bundled manual pages as ordinary notes;
# `man` falls back to it for page reads and searches it in query mode.
_MANUAL_PROJECT = "manual"

# API bound on directory recursion (directory_router depth query: ge=1, le=10).
_MAX_FIND_DEPTH = 10

# recent_activity's page-size cap; tail's `lines` maps onto it.
_MAX_TAIL_LINES = 100

# In-flight entity reads while projecting `find --fields`. The knowledge API has
# no bulk entity read, so a full page costs page_size GETs; this bounds how many
# are open at once — enough to hide per-request latency on a cloud-routed
# project, small enough not to flood the API with one tool call's fan-out.
# Deliberately not max_tokens-sliced: a slice param 404s on an entity with no
# markdown content (knowledge_router._apply_note_slice), which would turn a
# projected row into a failed find.
_FIELD_PROJECTION_CONCURRENCY = 8


@mcp.tool(
    title="Cat",
    description="Print a note's content. Accepts '<project>/path' identifiers.",
    tags={POSIX_TOOLS_TAG, "notes"},
    annotations={
        "title": "Cat",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def cat(
    identifier: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    section: Optional[str] = None,
    max_tokens: Optional[int] = None,
    include_frontmatter: bool = True,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """Print a note's content, optionally sliced by line range, section, or token budget.

    Args:
        identifier: Note title, permalink, memory:// URL, or '<project>/path'
            identifier (resolved exactly).
        start_line: First line to include (1-indexed, inclusive).
        end_line: Last line to include (inclusive). Defaults to the last line.
        section: Heading to slice to: "Decisions", path form "Auth/Decisions"
            to disambiguate by parent, or bracket form "Heading[1]" for the
            second duplicate heading. Cannot combine with start_line/end_line;
            the response's start_line/end_line support follow-up range reads.
        max_tokens: Approximate token budget. Longer content is truncated at a
            section or paragraph boundary with an explicit ellipsis marker; the
            response carries truncated/continue_line for resuming.
        include_frontmatter: Include the YAML frontmatter block in `content`.
            Ignored for section/max_tokens reads — those slices never carry a
            frontmatter block. A start_line/end_line range combined with
            max_tokens addresses the full document (frontmatter included) and
            therefore requires include_frontmatter=True.
        project: Project name. Optional - the server resolves the default.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        The read_note JSON payload (title, permalink, file_path, content,
        frontmatter), plus start_line/end_line/total_lines when a slice applied,
        `section` for section reads, and truncated/continue_line when max_tokens
        cut the content.
    """
    if section is not None and (start_line is not None or end_line is not None):
        raise ValueError(
            "cat: 'section' cannot be combined with start_line/end_line; use the "
            "returned start_line/end_line for follow-up range reads"
        )
    if max_tokens is not None and max_tokens < 1:
        raise ValueError(f"max_tokens must be >= 1, got {max_tokens}")
    if start_line is not None and start_line < 1:
        raise ValueError(f"start_line must be >= 1, got {start_line}")
    if end_line is not None and end_line < (start_line or 1):
        raise ValueError(f"end_line must be >= start_line, got {end_line}")
    # Trigger: a line range rides along with max_tokens while frontmatter is opted out.
    # Why: server-side line ranges are document-absolute (frontmatter included), but
    #      include_frontmatter=False range reads slice the frontmatter-stripped body —
    #      the same numbers would address different lines, and the served range could
    #      carry frontmatter text despite the explicit opt-out.
    # Outcome: the combination is rejected so every read keeps one coordinate system.
    if (
        max_tokens is not None
        and not include_frontmatter
        and (start_line is not None or end_line is not None)
    ):
        raise ValueError(
            "cat: max_tokens with start_line/end_line requires include_frontmatter=True — "
            "those ranges address the full document (frontmatter included); drop max_tokens "
            "for a body-relative range, or keep include_frontmatter=True"
        )

    # Trigger: section or max_tokens is set.
    # Why: those slices need the server-side section scan and token budgeting;
    #      plain line ranges keep their original client-side slicing untouched.
    # Outcome: the read carries the slice params (line bounds ride along as a
    #          lines= range) and the server-supplied payload returns as-is.
    server_side_slice = section is not None or max_tokens is not None
    lines_param: Optional[str] = None
    if server_side_slice and (start_line is not None or end_line is not None):
        lines_param = f"{start_line or 1}-{'' if end_line is None else end_line}"

    # '<project>/path' identifiers route to their project; route.path is the
    # identifier unchanged when no prefix was recognized.
    route = await resolve_project_path_route(
        identifier, project=project, project_id=project_id, context=context
    )
    if route.stripped and not route.path:
        raise ValueError(f"cat: '{identifier}' names a project, not a note")

    async with get_project_client(route.project, context=context, project_id=route.project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import KnowledgeClient, ResourceClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        entity_id = await knowledge_client.resolve_entity(route.path, strict=True)
        payload: dict[str, Any] = dict(
            await read_note_json_by_external_id(
                knowledge_client=knowledge_client,
                resource_client=ResourceClient(client, active_project.external_id),
                entity_external_id=entity_id,
                include_frontmatter=include_frontmatter,
                section=section,
                lines=lines_param,
                max_tokens=max_tokens,
            )
        )

    if server_side_slice or (start_line is None and end_line is None):
        return qualify_note_paths(payload, route)

    lines = str(payload["content"]).splitlines()
    total_lines = len(lines)
    first = start_line or 1
    last = min(end_line, total_lines) if end_line is not None else total_lines
    payload["content"] = "\n".join(lines[first - 1 : last])
    payload["start_line"] = first
    payload["end_line"] = last
    payload["total_lines"] = total_lines
    return qualify_note_paths(payload, route)


def _grep_retrieval_mode(literal: bool) -> SearchRetrievalMode:
    """Pick grep's retrieval mode: literal full-text on request, semantic when available."""
    if literal:
        return SearchRetrievalMode.FTS
    try:
        config = get_container().config
    except RuntimeError:
        # CLI paths call tools before the MCP container exists (search.py precedent).
        config = ConfigManager().config
    return SearchRetrievalMode.HYBRID if config.semantic_search_enabled else SearchRetrievalMode.FTS


@mcp.tool(
    title="Grep",
    description="Search note content for a pattern. Requires 'project' when several are addressable.",
    tags={POSIX_TOOLS_TAG, "search"},
    annotations={
        "title": "Grep",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def grep(
    pattern: str,
    literal: bool = False,
    page: int = 1,
    page_size: int = 10,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """Search note content, semantically by default.

    Args:
        pattern: Text to search for.
        literal: Force literal full-text matching instead of semantic search.
        page: Page number (1-indexed).
        page_size: Results per page.
        project: Project name. Required when more than one project is addressable.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        The search response as JSON: results, pagination, and totals.
    """
    if not pattern or not pattern.strip():
        raise ValueError("pattern must not be empty")
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    # grep's pattern is never parsed as a path — search text like "error/timeout"
    # must not be mistaken for a mount. Routing participates for the refusal rule
    # only: unqualified multi-project calls fail loudly instead of defaulting.
    route = await resolve_project_path_route(
        "", project=project, project_id=project_id, context=context
    )

    query = SearchQuery(
        text=pattern,
        retrieval_mode=_grep_retrieval_mode(literal),
        entity_types=[SearchItemType.ENTITY],
    )
    async with get_project_client(route.project, context=context, project_id=route.project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import SearchClient

        search_client = SearchClient(client, active_project.external_id)
        response = await search_client.search(query.model_dump(), page=page, page_size=page_size)
        return response.model_dump(mode="json", exclude_none=True)


async def _project_mount_listing(
    *, page: int, page_size: int, context: Context | None
) -> dict[str, Any]:
    """Render the addressable projects as directory entries (the mount-point view).

    Sources ``addressable_projects`` — the same set the path resolver routes by
    — so every mount advertised here is reachable as '<project>/path' (#1421).
    Each row's ``directory_path`` is the copyable '/<project>' prefix form, and
    the set already arrives sorted by project name.
    """
    rows = [
        DirectoryNode(
            name=item.name,
            directory_path=f"/{item.permalink}",
            permalink=item.permalink,
            type="directory",
        )
        for item in await addressable_projects(context=context)
    ]
    start = (page - 1) * page_size
    listing = DirectoryListResponse(
        nodes=rows[start : start + page_size],
        page=page,
        page_size=page_size,
        total=len(rows),
        has_more=start + page_size < len(rows),
    )
    return listing.model_dump(mode="json")


@mcp.tool(
    title="Ls",
    description="List one directory level. '/' lists projects; paths accept '<project>/path'.",
    tags={POSIX_TOOLS_TAG, "navigation"},
    annotations={
        "title": "Ls",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def ls(
    path: str = "/",
    page: int = 1,
    page_size: int = DEFAULT_DIRECTORY_PAGE_SIZE,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """List the immediate contents of one directory.

    Args:
        path: Directory path to list. '/' (the default) with no project param
            lists the active projects as mount points; '<project>/path' routes
            into that project.
        page: Page number (1-indexed).
        page_size: Nodes per page.
        project: Project name. Optional - '/' lists projects; qualified paths
            route themselves; other unqualified paths refuse when several
            projects are addressable.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        The directory listing as JSON: nodes, pagination, and totals.
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")
    if page_size > MAX_DIRECTORY_PAGE_SIZE:
        raise ValueError(f"page_size must be <= {MAX_DIRECTORY_PAGE_SIZE}, got {page_size}")

    # Trigger: bare root with no project addressed (param, UUID, or env constraint).
    # Why: the mount-point view puts project discovery in-band — ls "/" shows the
    #      mount table, ls "<project>" shows that project's root (#1415).
    # Outcome: list the active projects as directory entries; no project client.
    if (
        project is None
        and project_id is None
        and not os.environ.get("BASIC_MEMORY_MCP_PROJECT")
        and not path.strip().strip("/")
    ):
        return await _project_mount_listing(page=page, page_size=page_size, context=context)

    route = await resolve_project_path_route(
        path, project=project, project_id=project_id, context=context
    )
    list_path = f"/{route.path}" if route.stripped else path

    async with get_project_client(route.project, context=context, project_id=route.project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import DirectoryClient

        directory_client = DirectoryClient(client, active_project.external_id)
        listing = await directory_client.list(list_path, depth=1, page=page, page_size=page_size)
        return qualify_listing_paths(listing.model_dump(mode="json"), route)


def routed_listing_root(path: str, route: ProjectPathRoute) -> str:
    """The directory the returned paths are relative to, in *their* address space.

    Returned paths carry the project prefix whenever the call put it in the
    path, so the root a caller strips to rebuild a hierarchy must carry it too —
    and in the permalink spelling the payload uses, not the caller's ('My
    Research' vs 'my-research').
    """
    if not route.stripped or route.project is None:
        return path
    prefix = generate_permalink(route.project)
    return f"{prefix}/{route.path}" if route.path else prefix


async def find_listing(
    path: str = "/",
    *,
    name: Optional[str] = None,
    depth: int = _MAX_FIND_DEPTH,
    page: int = 1,
    page_size: int = DEFAULT_DIRECTORY_PAGE_SIZE,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> tuple[dict[str, Any], str]:
    """find's body: the listing, plus the root its paths are relative to.

    `bm tree` needs both halves — the listing to render and the root to strip —
    and resolving the path twice to get them cost a second project-list round
    trip on every cloud CLI call, since CLI calls carry no FastMCP context for
    the per-request cache to live in. One resolve now answers both.
    """
    if depth < 1 or depth > _MAX_FIND_DEPTH:
        raise ValueError(f"depth must be between 1 and {_MAX_FIND_DEPTH}, got {depth}")
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")
    if page_size > MAX_DIRECTORY_PAGE_SIZE:
        raise ValueError(f"page_size must be <= {MAX_DIRECTORY_PAGE_SIZE}, got {page_size}")

    # The directory API is project-scoped, so cross-project find does not exist:
    # find "/" with no project in a multi-project config refuses, teaching the
    # per-project '<project>/path' form instead.
    route = await resolve_project_path_route(
        path, project=project, project_id=project_id, context=context
    )
    list_path = f"/{route.path}" if route.stripped else path

    async with get_project_client(route.project, context=context, project_id=route.project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import DirectoryClient

        directory_client = DirectoryClient(client, active_project.external_id)
        listing = await directory_client.list(
            list_path,
            depth=depth,
            file_name_glob=name,
            page=page,
            page_size=page_size,
        )
        payload = qualify_listing_paths(listing.model_dump(mode="json"), route)

    return payload, routed_listing_root(path, route)


# --- find metadata predicates ---
# find's `meta` strings translate onto the search API's metadata_filters dict —
# the exact grammar parse_metadata_filters supports (eq, $gt/$gte/$lt/$lte, $in,
# array-contains-all, $between), nothing more. Word ops need whitespace around
# them and symbol ops exclude the key character class, so exactly one regex can
# match any given predicate. Two-char symbols sit first in the alternation so
# ">=" never parses as ">" plus a value starting with "=".
# The key capture admits '.' anywhere on purpose — it is looser than a dot path.
# parse_metadata_path, which owns the frontmatter path grammar, is what decides
# a well-formed one, applied in _parse_meta_predicates once the predicate has
# split. Tightening the capture instead would make '.owner=null' match no regex
# at all and be reported as a missing operator rather than as the bad key it is.
_PREDICATE_WORD_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s+(in|has|between)\s+(.+)$")
_PREDICATE_SYMBOL_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*(>=|<=|=|>|<)\s*(.*)$")
_SYMBOL_OPERATORS = {">": "$gt", ">=": "$gte", "<": "$lt", "<=": "$lte"}
_SUPPORTED_PREDICATE_OPS = "= > >= < <= in has between"
# The symbol regex consumes the first operator it recognizes, so an operator
# spelling outside the supported set ("==", "=>", ">>", ">=>") leaves its
# second character at the head of the value. These are the characters that can
# be left behind that way. A set, not a string: "" is a substring of any string
# but is not a member here, so an empty token never reads as operator-prefixed.
_OPERATOR_VALUE_PREFIXES = frozenset("=<>")
# Mirrors search_notes' alias: "note_type" (the entity model column) means the
# frontmatter "type" key, so the two surfaces accept the same spelling.
_METADATA_KEY_ALIASES = {"note_type": "type"}


def _opens_an_unterminated_quote(text: str) -> bool:
    """True when a double quote opens in `text` and nothing closes it.

    One scanner decides this for every value token, scalar or list element, so
    the two paths cannot disagree about what "quoted" means.
    """
    in_quotes = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif in_quotes and char == "\\":
            escaped = True
        elif char == '"':
            in_quotes = not in_quotes
    return in_quotes


def _predicate_scalar(token: str, predicate: str) -> Any:
    """Read one predicate value token, refusing everything a search cannot answer.

    "true"/"false"/"null"/numbers become bool/None/int/float so the produced
    filters dict is byte-equal to what a rich search_notes caller passes as
    JSON; a JSON-quoted token ('"true"') forces a literal string; anything that
    is not a JSON scalar stays the raw string.
    """
    text = token.strip()
    # Trigger: an unquoted value opens with one of the operator characters.
    # Why: only the operators in _SUPPORTED_PREDICATE_OPS are real, but the
    #      regexes match the longest supported one and hand the rest to the
    #      value — 'status==active' would filter for the string "=active" and
    #      'count>>3' for ">3", so a typo'd operator answered as an empty (or
    #      worse, a non-empty but wrong) result set instead of the refusal the
    #      grammar documents.
    # Outcome: refuse, naming the supported set and the quoting escape hatch a
    #          value that genuinely starts with '=', '<' or '>' needs.
    if text[:1] in _OPERATOR_VALUE_PREFIXES:
        raise ValueError(
            f"find: unsupported predicate operator in '{predicate}'; "
            f"supported: {_SUPPORTED_PREDICATE_OPS}; quote the value as "
            f'"{text}" to match text that starts with that character'
        )
    # Trigger: a quote opens in the token and never closes.
    # Why: json.loads rejects it, and the raw-text fallback would then keep the
    #      dangling quote as part of a literal value — 'status="active' would
    #      search for the seven-character text '"active', report no matches,
    #      and hide the typo behind an ordinary empty result.
    # Outcome: refuse for every value token, so the scalar operators and the
    #          list operators (where a severed quote would also mis-split the
    #          list) answer a dangling quote the same way.
    if _opens_an_unterminated_quote(text):
        raise ValueError(
            f"find: predicate '{predicate}' has an unterminated quoted value; "
            "close the quote — 'status=\"active\"' forces a literal string, and "
            "'label in \"a,b\",c' protects a comma inside a list element"
        )
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    # Trigger: the token parsed to a non-finite float. Python's JSON reader
    #          accepts NaN/Infinity/-Infinity as an extension, and overflows a
    #          large exponent ("1e999") to infinity.
    # Why: none of those are JSON the request encoder will emit, so the filter
    #      died at transport with "Out of range float values are not JSON
    #      compliant" — a network-shaped error for what is a predicate typo.
    # Outcome: refuse here, in the same shape as the grammar's other refusals.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(
            f"find: predicate '{predicate}' has a non-finite number '{text}'; "
            f'predicate values must be finite numbers; quote the value as "{text}" '
            "to match that literal text"
        )
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return text


def _split_predicate_items(raw_value: str, predicate: str) -> list[str]:
    """Split a list-op value on its top-level commas, refusing empty elements.

    A comma inside a JSON-quoted token belongs to the value, not to the list, so
    the quoting escape hatch the scalar operators document works for `in`, `has`
    and `between` too: 'label in "a,b",c' yields ['"a,b"', 'c'], which
    _predicate_scalar then reads as the literal strings "a,b" and "c". Splitting
    the raw string first would sever the quoted token into '"a' and 'b"' and
    filter for values nothing carries — wrong, and silent.

    A split only happens outside quotes, so an unterminated quote here always
    ends up inside one element and _predicate_scalar refuses it; this function
    does not repeat that check.
    """
    items: list[str] = []
    current: list[str] = []
    in_quotes = False
    escaped = False
    for char in raw_value:
        current.append(char)
        if escaped:
            escaped = False
        elif in_quotes and char == "\\":
            escaped = True
        elif char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            current.pop()
            items.append("".join(current))
            current = []
    items.append("".join(current))
    stripped = [item.strip() for item in items]
    if any(not item for item in stripped):
        raise ValueError(f"find: predicate '{predicate}' has an empty list element")
    return stripped


def _refuse_null_outside_equality(values: list[Any], op: str, predicate: str) -> None:
    """Refuse a null bound, list element, or comparison value.

    Trigger: `null` reached an operator other than '='.
    Why: '=' compiles to IS NULL server-side, which is the question null asks —
         does this note carry a value here at all. Every other operator compares
         against the value, and a SQL comparison with NULL is never true, so
         'score>null' or 'priority in null,high' would answer zero rows for
         every note in the project rather than name the query it cannot run.
    Outcome: refuse, pointing at the equality spelling that does work.
    """
    if any(value is None for value in values):
        raise ValueError(
            f"find: predicate '{predicate}' uses null with '{op}'; null matches only "
            "as equality ('owner=null' finds notes carrying no owner); quote the "
            'value as "null" to match that literal text'
        )


def _parse_meta_predicates(predicates: list[str]) -> dict[str, Any]:
    """Translate POSIX-style predicate strings into the search API metadata_filters dict.

    One predicate per string; predicates AND together. Exactly one predicate
    per key — the API admits one operator per key, so a repeated key fails fast
    instead of last-wins. Raises ValueError (surfaced to MCP callers as
    ToolError) on any operator outside the supported set, and on any key outside
    the search API's dot-path grammar.
    """
    filters: dict[str, Any] = {}
    for predicate in predicates:
        match = _PREDICATE_WORD_RE.match(predicate.strip()) or _PREDICATE_SYMBOL_RE.match(
            predicate.strip()
        )
        if match is None:
            raise ValueError(
                f"find: unsupported predicate operator in '{predicate}'; "
                f"supported: {_SUPPORTED_PREDICATE_OPS}"
            )
        raw_key, op, raw_value = match.groups()
        key = _METADATA_KEY_ALIASES.get(raw_key, raw_key)
        # Trigger: the key capture accepted something that is not a dot path —
        #          a doubled, leading or trailing dot ('review..approved',
        #          '.owner', 'owner.').
        # Why: the search API refuses these keys, so the query was never going
        #      to run. Letting it travel spends a request to come back with
        #      "Unsupported metadata filter key", which names neither find nor
        #      the shape a key must have — every other predicate mistake is
        #      refused here, before transport, in find's own words.
        # Outcome: refuse locally, naming the offending key and the grammar.
        if parse_metadata_path(key) is None:
            raise ValueError(
                f"find: malformed predicate key '{key}' in '{predicate}'; keys are "
                "dot-separated names of letters, digits, '_' or '-' "
                "(e.g. 'status' or 'review.approved')"
            )
        if key in filters:
            raise ValueError(
                f"find: duplicate predicate key '{key}' in '{predicate}'; "
                "use 'between' for ranges (e.g. 'score between 0.3,0.8')"
            )
        if op in ("in", "has", "between"):
            items = [
                _predicate_scalar(item, predicate)
                for item in _split_predicate_items(raw_value, predicate)
            ]
            _refuse_null_outside_equality(items, op, predicate)
            if op == "between" and len(items) != 2:
                raise ValueError(f"find: 'between' needs exactly min,max in '{predicate}'")
            filters[key] = (
                {"$in": items} if op == "in" else items if op == "has" else {"$between": items}
            )
        else:
            if not raw_value.strip():
                raise ValueError(f"find: predicate '{predicate}' has no value")
            value = _predicate_scalar(raw_value, predicate)
            if op != "=":
                _refuse_null_outside_equality([value], op, predicate)
            filters[key] = value if op == "=" else {_SYMBOL_OPERATORS[op]: value}
    return filters


def _project_metadata_fields(
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


@mcp.tool(
    title="Find",
    description=(
        'Recursively list files by name glob or metadata predicates (e.g. "status=active"). '
        "Paths accept '<project>/path'."
    ),
    tags={POSIX_TOOLS_TAG, "navigation"},
    annotations={
        "title": "Find",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def find(
    path: str = "/",
    name: Optional[str] = None,
    depth: int = _MAX_FIND_DEPTH,
    page: int = 1,
    page_size: int = DEFAULT_DIRECTORY_PAGE_SIZE,
    meta: Annotated[Optional[list[str]], BeforeValidator(coerce_list)] = None,
    fields: Annotated[Optional[list[str]], BeforeValidator(coerce_list)] = None,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """Recursively list files by name glob, or query notes by frontmatter metadata.

    Without `meta`, this is a recursive directory listing. With `meta`, find
    routes through the metadata search instead: predicates AND together, `path`
    still scopes the results — server-side, by the indexed file path, so a note
    is scoped by where it actually lives rather than by a permalink that may no
    longer say — and non-markdown files (which carry no frontmatter) are never
    hits. `name` and `depth` are refused alongside `meta`: the search API has no
    filename-glob or depth-bound facility, and silently ignoring either would
    misreport the match set.

    Args:
        path: Directory to start from (default: project root). '<project>/path'
            routes into that project. With `meta`, scopes the search to this
            subtree (matched on a directory boundary, so "specs" never admits
            "specs-archive/").
        name: File-name glob to match, e.g. "*.md". None matches everything.
            Cannot combine with `meta` — scope with `path` instead.
        depth: How many levels to recurse (1-10, default: 10). A non-default
            depth cannot combine with `meta` (the subtree scope is
            all-or-nothing).
        page: Page number (1-indexed).
        page_size: Nodes per page.
        meta: Frontmatter metadata predicates, repeatable; every predicate must
            hold. One predicate per string, one predicate per key, at least one
            predicate (omit `meta` for the directory listing):
              "status=active"              equality
              "confidence>0.6"             comparison: > >= < <=
              "priority in high,critical"  any of the listed values
              "tags has security,oauth"    array contains ALL listed values
              "score between 0.3,0.8"      inclusive range
              "owner=null"                 key missing or explicitly null
            Values are JSON-scalar inferred ("true"/"false"/"null"/numbers
            become booleans/None/numbers); quote a token to force a literal
            string (e.g. 'status="true"'), including inside a list, where the
            quotes also protect a comma ('label in "a,b",c' matches "a,b" or
            "c") and a value that itself starts with an operator character
            ('range=">=5"'). Numbers must be finite, and null is only meaningful
            with "=" — the other operators compare against the value, and a
            comparison with null is never true. Keys accept dot-paths
            ("review.approved"); "note_type" aliases the frontmatter "type" key.
            Any other operator fails fast naming the supported set.
        fields: Frontmatter fields to return per hit (dot-paths allowed), e.g.
            ["title", "priority"]. Requires `meta`. A field missing on a hit
            renders as null — rows are never dropped.
        project: Project name. Optional - qualified paths route themselves;
            unqualified paths refuse when several projects are addressable.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        Without `meta`: the directory listing as JSON (nodes, pagination,
        totals). With `meta`: the search response as JSON (results, pagination,
        totals); each result carries a `fields` object when `fields` was
        requested.
    """
    # Combination rules, before any I/O. The metadata search takes no filename
    # glob and no depth bound, so `name` and `depth` are refused rather than
    # silently ignored; `path` survives, because the search API does express a
    # file-path subtree. `fields` is the SELECT to the predicates' WHERE;
    # without predicates the directory listing stays byte-identical to today.
    if meta is not None:
        # Trigger: 'meta' present but carrying no predicates.
        # Why: an empty list parses to an empty filters dict, which is not None
        #      and would route into the metadata search with no predicate at
        #      all — an unfiltered project-wide match where the caller asked for
        #      a filtered set, and not the directory listing either.
        # Outcome: refuse, exactly as 'fields' refuses an empty list.
        if not meta:
            raise ValueError(
                "find: 'meta' must carry at least one predicate — omit 'meta' entirely "
                "for the plain directory listing"
            )
        if name is not None:
            raise ValueError(
                "find: 'name' cannot combine with 'meta' — the metadata search has no "
                "filename glob; scope with 'path' instead"
            )
        if depth != _MAX_FIND_DEPTH:
            raise ValueError(
                "find: 'depth' cannot combine with 'meta' — the metadata search scopes "
                "by whole subtree; scope with 'path' instead"
            )
    projected_fields: list[MetadataPath] | None = None
    if fields is not None:
        if meta is None:
            raise ValueError(
                "find: 'fields' requires 'meta' predicates — without predicates find "
                "returns the plain directory listing"
            )
        fields = [field_name.strip() for field_name in fields]
        if not fields or any(not field_name for field_name in fields):
            raise ValueError("find: 'fields' entries must be non-empty frontmatter field names")
        # Trigger: a field path that is not a dot path ('review..approved',
        #          '.owner', 'owner.').
        # Why: projection answers a missing field with null, so an empty segment
        #      walked to null for every hit and read exactly like a field the
        #      notes genuinely do not carry — a typo returning a uniform,
        #      plausible, wrong answer, after paying the search and one entity
        #      GET per hit. Predicates at least reached a server that refused
        #      them; this one had nothing to fail against.
        # Outcome: refuse before routing, through the same parse the predicate
        #          keys use, so the two cannot diverge.
        projected_fields = []
        for field_name in fields:
            field_path = parse_metadata_path(field_name)
            if field_path is None:
                raise ValueError(
                    f"find: malformed field path '{field_name}'; field paths are "
                    "dot-separated names of letters, digits, '_' or '-' "
                    "(e.g. 'title' or 'review.approved')"
                )
            projected_fields.append(field_path)
    metadata_filters = _parse_meta_predicates(meta) if meta is not None else None

    # Trigger: predicates are present, so this call queries metadata rather than
    #          walking directories.
    # Why: the two arms call different project-scoped APIs, and each resolves the
    #      route exactly once — find_listing already owns validation and
    #      resolution for the listing arm, so branching before resolving keeps a
    #      routed call to one project-list round trip (which is why find_listing
    #      exists at all).
    # Outcome: the metadata arm answers with search results; otherwise the
    #          directory listing comes back unchanged.
    if metadata_filters is not None:
        # Pagination is an argument check, so it refuses here with the rest of
        # them, before any I/O. The listing arm inherits these same bounds from
        # find_listing, which the metadata arm never reaches — stated once per
        # arm rather than once for both, so neither can drift onto the other's
        # error message.
        if page < 1:
            raise ValueError(f"page must be >= 1, got {page}")
        if page_size < 1:
            raise ValueError(f"page_size must be >= 1, got {page_size}")
        if page_size > MAX_DIRECTORY_PAGE_SIZE:
            raise ValueError(f"page_size must be <= {MAX_DIRECTORY_PAGE_SIZE}, got {page_size}")

        # The search API is project-scoped too, so cross-project find does not
        # exist here either: an unqualified path in a multi-project config
        # refuses, teaching the per-project '<project>/path' form instead.
        route = await resolve_project_path_route(
            path, project=project, project_id=project_id, context=context
        )
        return await _find_by_metadata(
            route_project=route.project,
            # route.path is the caller's input verbatim when no project prefix
            # was recognized, so one value covers both routed and raw spellings.
            # SearchQuery.file_path_prefix is the boundary parser for it: it
            # reads "./specs" the way the directory listing does, and collapses
            # every root spelling — including the "" a bare '<project>' routes
            # to, a mount point rather than a subtree — onto "no scope".
            scope=route.path,
            metadata_filters=metadata_filters,
            fields=projected_fields,
            page=page,
            page_size=page_size,
            # The route's id, not the caller's raw param: it is what keeps a
            # cloud mount bound to the workspace whose listing advertised it.
            project_id=route.project_id,
            context=context,
        )

    listing, _ = await find_listing(
        path,
        name=name,
        depth=depth,
        page=page,
        page_size=page_size,
        project=project,
        project_id=project_id,
        context=context,
    )
    return listing


async def _find_by_metadata(
    *,
    route_project: Optional[str],
    scope: str,
    metadata_filters: dict[str, Any],
    fields: Optional[list[MetadataPath]],
    page: int,
    page_size: int,
    project_id: Optional[str],
    context: Context | None,
) -> dict[str, Any]:
    """find's metadata arm: one search call, plus per-hit field projection.

    The listing arm's counterpart to ``find_listing``; `find` has already bounded
    the pagination and refused `name` and `depth` against `meta`.

    The path scope composes server-side as a file-path prefix — the indexed
    `file_path`, which is where the note actually lives, not its permalink,
    which stops mirroring that path once a note pins one in frontmatter or is
    moved with update_permalinks_on_move disabled (the default). It ANDs with
    the metadata filters in the same WHERE, so the total the server reports is
    the real match count for the scope that ran, and every page of it is
    reachable.
    """
    async with get_project_client(route_project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import KnowledgeClient, SearchClient

        query = SearchQuery(
            # Normalized by the field validator, which maps every root spelling
            # onto None: the predicates are then the whole WHERE.
            file_path_prefix=scope,
            metadata_filters=metadata_filters,
            entity_types=[SearchItemType.ENTITY],
        )
        search_client = SearchClient(client, active_project.external_id)
        response = await search_client.search(query.model_dump(), page=page, page_size=page_size)
        payload = response.model_dump(mode="json", exclude_none=True)
        if not fields:
            return payload

        # Field projection hydrates from the entity's full normalized
        # frontmatter — the search hit's own `metadata` is index-row metadata,
        # not the canonical projection source. One GET per hit is unavoidable
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

        limiter = asyncio.Semaphore(_FIELD_PROJECTION_CONCURRENCY)

        async def entity_metadata(entity_external_id: str) -> dict[str, Any] | None:
            async with limiter:
                entity = await knowledge_client.get_entity(entity_external_id)
            return entity.entity_metadata

        # Trigger: any one projection read fails — a hit deleted between the
        #          search and its hydration, or a cloud-routed GET erroring.
        # Why: gather raises the first failure but leaves its siblings running,
        #      and this function then unwinds out of get_project_client, which
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
        # Injected post-dump so null field values survive exclude_none.
        for row, metadata in zip(payload["results"], hydrated, strict=True):
            row["fields"] = _project_metadata_fields(metadata, fields)
    return payload


@mcp.tool(
    title="Tail",
    description="Show recently changed notes. Requires 'project' when several are addressable.",
    tags={POSIX_TOOLS_TAG, "navigation", "notes"},
    annotations={
        "title": "Tail",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def tail(
    timeframe: str = "7d",
    lines: int = 10,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> list[dict[str, Any]]:
    """Show the most recently changed notes in a project.

    Args:
        timeframe: Time window, e.g. "7d", "yesterday", "2 days ago".
        lines: Maximum number of rows to return (1-100).
        project: Project name. Required when more than one project is addressable.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        Rows of {type, title, permalink, file_path, created_at}, newest first.
    """
    if lines < 1:
        raise ValueError(f"lines must be >= 1, got {lines}")
    if lines > _MAX_TAIL_LINES:
        raise ValueError(f"lines must be <= {_MAX_TAIL_LINES}, got {lines}")

    # tail has no path to carry a project prefix, so routing participates for
    # the refusal rule only: unqualified multi-project calls fail loudly.
    route = await resolve_project_path_route(
        "", project=project, project_id=project_id, context=context
    )

    async with get_project_client(route.project, context=context, project_id=route.project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import MemoryClient

        memory_client = MemoryClient(client, active_project.external_id)
        activity = await memory_client.recent(
            timeframe=timeframe,
            depth=1,
            types=[SearchItemType.ENTITY.value],
            page=1,
            page_size=lines,
        )

    rows: list[dict[str, Any]] = []
    for result in activity.results:
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


@mcp.tool(
    title="Man",
    description="Look up a manual page or search the manual.",
    tags={POSIX_TOOLS_TAG, "notes"},
    annotations={
        "title": "Man",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def man(
    page: Optional[str] = None,
    query: Optional[str] = None,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Look up one manual page, search the manual, or render the index.

    Modes:
    - No arguments: the manual index (apropos view), as markdown.
    - page: one page — a bundled page by reference (e.g. "search-notes(3)"),
      else a note read from the manual project.
    - query: search notes of type "manpage" in the manual project.

    Args:
        page: Page reference, e.g. "search-notes(3)". Any common spelling works.
        query: Apropos search text over manpage notes. Mutually exclusive with `page`.
        project: Manual project name (default: "manual"). Bundled pages need no project.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        Markdown (index or one page) or a search response as JSON.
    """
    if page is not None and query is not None:
        raise ValueError("man: pass either 'page' or 'query', not both")

    if page is None and query is None:
        # Mirror the memory://man resource: mark pages whose tool this server
        # does not register so an agent does not call a tool that is not there.
        tools = await mcp.list_tools(run_middleware=False)
        return render_index(bundled_pages(), frozenset(tool.name for tool in tools))

    manual_project = project or _MANUAL_PROJECT

    if page is not None:
        try:
            page_ref = parse_page_ref(page)
        except ValueError:
            bundled = None
        else:
            bundled = find_page(page_ref)
        if bundled is not None:
            return bundled.read()

        # Not a bundled page — the reference may name a manual note (the
        # non-bundled sections live as notes in the manual project), mirroring
        # the memory://man resource fallback.
        async with get_project_client(manual_project, context=context, project_id=project_id) as (
            client,
            active_project,
        ):
            # Import here to avoid circular import
            from basic_memory.mcp.clients import KnowledgeClient, ResourceClient

            knowledge_client = KnowledgeClient(client, active_project.external_id)
            resource_client = ResourceClient(client, active_project.external_id)
            try:
                entity_id = await knowledge_client.resolve_entity(page, strict=True)
            except ToolError as error:
                # Neither a bundled page nor a manual note — the manual's hint
                # is the useful error, not the raw resolution failure. Only the
                # resolve miss means "no such entry"; a failed read of a note
                # that DID resolve is an operational error and propagates as-is.
                raise ToolError(f"No manual entry for {page}") from error
            response = await resource_client.read(entity_id)
            return response.text

    search_query = SearchQuery(
        text=query,
        note_types=["manpage"],
        entity_types=[SearchItemType.ENTITY],
    )
    async with get_project_client(manual_project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import SearchClient

        search_client = SearchClient(client, active_project.external_id)
        response = await search_client.search(search_query.model_dump(), page=1, page_size=10)
        return response.model_dump(mode="json", exclude_none=True)


# Default-hidden until the composition root reads config in lifespan. Keeps the
# tool listing identical to today for any consumer that lists before startup.
set_posix_tools_visibility(mcp, False)
