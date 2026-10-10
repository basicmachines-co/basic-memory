"""Write note tool for Basic Memory MCP server."""

import dataclasses
import json
import textwrap
from collections.abc import Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    Annotated,
    List,
    Literal,
    NoReturn,
    Optional,
    Union,
    assert_never,
)

import logfire
from httpx import HTTPStatusError
from loguru import logger
from pydantic import AliasChoices, BeforeValidator, Field

from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.file_utils import remove_frontmatter
from basic_memory.mcp.project_context import get_project_client, add_project_metadata
from basic_memory.mcp.server import mcp
from fastmcp import Context
from fastmcp.exceptions import ToolError
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
)
from basic_memory.schemas.base import Entity
from basic_memory.schemas.v2.note_write import (
    NoteCreated,
    NoteUpdated,
    NoteAlreadyExists,
    NoteTargetMoved,
    NoteLocked,
    NoteRevisionConflict,
)
from basic_memory.schemas.search import (
    SearchItemType,
    SearchQuery,
    SearchResult,
    SearchRetrievalMode,
)
from basic_memory.utils import (
    build_qualified_permalink_reference,
    coerce_dict,
    parse_tags,
    validate_project_path,
)
from basic_memory.workspace_context import current_workspace_permalink_context

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.mcp.clients import SearchClient

# Define TagType as a Union that can accept either a string or a list of strings or None
TagType = Union[List[str], str, None]

# --- Similar-note advisory -------------------------------------------------------------
# How many neighbors to surface after a create. Ranking is reliable enough to put the note
# an agent probably meant at the top; a longer list adds noise, not signal.
SIMILAR_NOTES_LIMIT = 3
# The vector index embeds a note's title and opening content as its first chunk, so the
# probe copies that shape and length to land in the same neighborhood as the note itself.
SIMILAR_NOTES_PROBE_CHARS = 900
# The advisory asks "might this be the same note?", a stricter question than search's
# "is this relevant at all?" (semantic_min_similarity, 0.55). Measured on the default
# bge-small-en-v1.5 model (#1718): rewrites of an existing note scored 0.77-0.93 against
# it (0.78-0.87 on the Moby Dick vault, #1259), while the nearest neighbor of a note on an
# unrelated topic topped out at 0.67. Below this floor the list was only noise; near the
# top, a duplicate and a closely related note still share one band, so ranking decides.
# The number holds for that model only; see similar_notes_min_similarity.
SIMILAR_NOTES_MIN_SIMILARITY = 0.70


@dataclasses.dataclass(frozen=True, slots=True)
class SimilarNote:
    """An existing note the search index ranked closest to a freshly created one."""

    title: str
    permalink: str | None
    file_path: str


def _compose_similarity_probe(title: str, content: str) -> str:
    """Build the text used to look for existing notes near a freshly written one."""
    body = remove_frontmatter(content)
    return f"{title}\n\n{body}"[:SIMILAR_NOTES_PROBE_CHARS].strip()


def similar_notes_min_similarity(config: BasicMemoryConfig) -> float | None:
    """Return the advisory's similarity floor, or None to use the server's own.

    Cosine scores are model-specific: on OpenAI's text-embedding-3-small correct
    paraphrases cluster near 0.37, so the floor measured on the default model would hide
    real duplicates there. Only the exact embedding setup it was measured on gets the
    floor; anything else keeps semantic_min_similarity, which users tune for their model.
    The setup is read from this process's config, which describes the server a local
    write reaches; a write routed elsewhere with a different local setup falls back to
    that server's floor, the behavior before the advisory floor existed.
    """
    # Only FastEmbed can be the measured setup; checking it first also keeps the
    # identity resolution away from provider configs it would reject.
    if config.semantic_embedding_provider.strip().lower() != "fastembed":
        return None
    # The persisted embedding identity is what decides whether stored vectors are
    # reusable, so it is the definition of "the same embeddings": it covers the model,
    # dimensions, and document/query prefixes.
    measured = configured_embedding_provider_identity(BasicMemoryConfig())
    if configured_embedding_provider_identity(config) != measured:
        return None
    # A user who raised the search floor above the advisory's keeps the stricter one.
    return max(config.semantic_min_similarity, SIMILAR_NOTES_MIN_SIMILARITY)


def similar_notes_query(title: str, content: str, *, min_similarity: float | None) -> SearchQuery:
    """Build the search that asks which existing notes might be the one just written.

    Vector-only retrieval keeps the probe out of the FTS query parser, which would read
    parentheses and boolean words in ordinary prose as operators.
    """
    return SearchQuery(
        text=_compose_similarity_probe(title, content),
        retrieval_mode=SearchRetrievalMode.VECTOR,
        entity_types=[SearchItemType.ENTITY],
        min_similarity=min_similarity,
    )


def _collapse_similar_notes(
    results: Sequence[SearchResult],
    *,
    exclude_file_path: str,
    exclude_permalink: str | None,
    limit: int,
) -> list[SimilarNote]:
    """Drop the note that was just written and keep one row per remaining note.

    The new note's own row may or may not be indexed yet — vectors publish in the
    background — so it is removed when present rather than expected.
    """
    similar_notes: list[SimilarNote] = []
    seen_paths: set[str] = set()
    for result in results:
        is_new_note = result.file_path == exclude_file_path or (
            exclude_permalink is not None and result.permalink == exclude_permalink
        )
        if is_new_note or result.file_path in seen_paths:
            continue
        seen_paths.add(result.file_path)
        similar_notes.append(
            SimilarNote(title=result.title, permalink=result.permalink, file_path=result.file_path)
        )
        if len(similar_notes) == limit:
            break
    return similar_notes


async def _find_similar_notes(
    search_client: "SearchClient",
    *,
    title: str,
    content: str,
    exclude_file_path: str,
    exclude_permalink: str | None,
    min_similarity: float | None,
) -> list[SimilarNote]:
    """Ask the vector index which existing notes sit closest to the note just written."""
    query = similar_notes_query(title, content, min_similarity=min_similarity)
    # One extra row leaves room for the new note's own hit before collapsing.
    response = await search_client.search(
        query.model_dump(), page=1, page_size=SIMILAR_NOTES_LIMIT + 1
    )
    return _collapse_similar_notes(
        response.results,
        exclude_file_path=exclude_file_path,
        exclude_permalink=exclude_permalink,
        limit=SIMILAR_NOTES_LIMIT,
    )


def _probe_declined_by_server(error: BaseException) -> bool:
    """Return whether the search API refused the probe outright (HTTP 400).

    That is how the API answers when semantic search is disabled or has no embedding
    provider: a configuration state of the routed server, not a failure worth a warning
    on every create.
    """
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, HTTPStatusError):
            return current.response.status_code == 400
        current = current.__cause__
    return False


def _format_similar_notes_section(
    similar_notes: Sequence[SimilarNote], *, new_permalink: str | None
) -> str:
    """Render the advisory as a question with the ways out spelled out.

    Similarity scores are deliberately absent: on the default model a near-duplicate and
    a merely related note score in the same band, so a number would imply a precision the
    signal does not have. Ranking is the useful part.
    """
    closest = similar_notes[0]
    closest_ref = closest.permalink or closest.file_path
    lines = [
        "",
        "## Similar existing notes",
        "This note looks similar to notes that already exist. "
        "Did you mean to write to one of these?",
        *(f"- {note.title} (`{note.permalink or note.file_path}`)" for note in similar_notes),
        "",
        "| If it is... | Then |",
        "|---|---|",
        f'| The same topic | `read_note("{closest_ref}")`, then '
        f'`edit_note("{closest_ref}", operation="append", content="...")` '
        f'and `delete_note("{new_permalink}")` |',
        f"| Related but distinct | Add a relation here, e.g. `- relates_to [[{closest.title}]]` |",
        "| Unrelated | Nothing to do |",
    ]
    return "\n".join(lines)


def _compose_workspace_project_route(
    *,
    workspace: Optional[str],
    project: Optional[str],
    project_id: Optional[str],
) -> Optional[str]:
    """Return the explicit project route requested by workspace/project args."""
    if workspace is None:
        return project

    cleaned_workspace = workspace.strip().strip("/")
    if not cleaned_workspace:
        raise ValueError("workspace must not be empty when provided")
    if "/" in cleaned_workspace:
        raise ValueError("workspace must be a single workspace slug, name, or tenant_id")
    if project_id is not None:
        raise ValueError("workspace cannot be combined with project_id; use project_id alone")
    if project is None or not project.strip().strip("/"):
        raise ValueError("workspace requires an explicit project argument")

    cleaned_project = project.strip().strip("/")
    if "/" in cleaned_project:
        raise ValueError(
            "Use either workspace='workspace' with project='project', "
            "or project='workspace/project', not both"
        )
    return f"{cleaned_workspace}/{cleaned_project}"


@mcp.tool(
    title="Write Note",
    description=(
        "Create a markdown note. If the note already exists, returns an error by default "
        "— pass overwrite=True to replace. directory is required. For incremental changes "
        "to an existing note use edit_note. A new note's result may list similar existing "
        "notes; that is advisory and does not block the write. Pass expected_checksum with "
        "overwrite=True to replace the note only if it is still the revision you read."
    ),
    tags={"notes"},
    annotations={
        "title": "Write Note",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def write_note(
    title: str,
    content: str,
    # Folder/dir/path are interchangeable in models' training data.
    directory: Annotated[
        str,
        Field(validation_alias=AliasChoices("directory", "folder", "dir", "path")),
    ],
    project: Optional[str] = None,
    workspace: Optional[str] = None,
    project_id: Optional[str] = None,
    tags: list[str] | str | None = None,
    note_type: str = "note",
    metadata: Annotated[dict[str, Any] | None, BeforeValidator(coerce_dict)] = None,
    overwrite: bool | None = None,
    expected_checksum: str | None = None,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Write a markdown note to the knowledge base.

    Creates a markdown note with semantic observations and relations.
    If the note already exists, returns an error by default. Pass overwrite=True
    to replace the existing note. For incremental updates, use edit_note instead.

    Project Resolution:
    Server resolves projects using a unified priority chain (same in local and cloud modes):
    Single Project Mode → project parameter → default project.
    Uses default project automatically. Specify `project` parameter to target a different project.

    The content can include semantic observations and relations using markdown syntax:

    Observations format:
        `- [category] Observation text #tag1 #tag2 (optional context)`

        Examples:
        `- [design] Files are the source of truth #architecture (All state comes from files)`
        `- [tech] Using SQLite for storage #implementation`
        `- [note] Need to add error handling #todo`

    Relations format:
        - Explicit: `- relation_type [[Entity]] (optional context)`
        - Quoted: `- "multi word relation type" [[Entity]] (optional context)`
        - Quoted: `- 'multi word relation type' [[Entity]] (optional context)`
        - Disambiguation: Add `#bm:links_to` when prose before `[[Entity]]`
          must not be treated as a single-token relation type
        - Inline: Any other `[[Entity]]` reference creates a `links_to` relation

        Examples:
        `- depends_on [[Content Parser]] (Need for semantic extraction)`
        `- "based on" [[Design Notes]]`
        `- 'in response to' [[Incident Review]]`
        `- Mother [[Alice]] #bm:links_to`
        `- implements [[Search Spec]] (Initial implementation)`
        `- This feature extends [[Base Design]] and uses [[Core Utils]]`

    Args:
        title: The title of the note; written to frontmatter and drives the permalink.
            No H1 is added for you: content is saved as given, so include a
            "# Title" heading yourself if the note should open with one.
        content: Markdown content for the note, can include observations and relations.
            May carry its own frontmatter; a `type:` in content frontmatter takes
            precedence over the note_type parameter.
        directory: Directory path relative to project root where the file should be saved.
                   Use forward slashes (/) as separators. Use "/" or "" to write to project root.
                   Examples: "notes", "projects/2025", "research/ml", "/" (root).
                   MCP accepts the aliases folder, dir, and path; the CLI flag is --folder.
        project: Project name to write to. Optional. Omitting both project and project_id
                writes to the session's active project (the last one this session
                touched), and only falls back to the configured default project when there
                is none — so
                after working in another project, pass project explicitly. Use
                "workspace/project" to route to a project in a specific cloud workspace.
                A bare name that exists in multiple workspaces resolves to the default
                workspace, so use the qualified form (or project_id) to disambiguate. If
                unknown, use list_memory_projects() to discover available projects and
                their qualified names.
        workspace: Workspace slug, name, or tenant_id. When provided with `project`,
                routes as `workspace/project`. Cannot be combined with `project_id`.
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from list_memory_projects().
        tags: Tags to categorize the note. Can be a list of strings, a comma-separated string, or None.
              Note: If passing from external MCP clients, use a string format (e.g. "tag1,tag2,tag3")
        note_type: Type of note to create (stored in frontmatter `type:`). Defaults to "note".
                   Can be "guide", "report", "config", "person", etc. The CLI flag is --type.
                   A `type:` in content frontmatter takes precedence over this parameter, and
                   this is what schema validation keys on.
        metadata: Optional dict of extra frontmatter fields merged into entity_metadata.
                  Useful for schema notes or any note that needs custom YAML frontmatter
                  beyond title/type/tags. Nested dicts are supported. Not available from the CLI.
        overwrite: If True, replace existing note on conflict. If False, error on conflict.
                   If None (default), consult write_note_overwrite_default config setting.
                   Overwrite replaces the body but merges frontmatter: keys the existing
                   note has and this write does not set (custom fields) are kept, while
                   keys this write sets (title, type, tags, metadata, or frontmatter in
                   content) replace the old values. Existing frontmatter that cannot be
                   parsed is discarded rather than merged. To remove a key, rewrite it
                   with edit_note or edit the file.
        expected_checksum: Optional revision precondition for overwrite=True: the checksum
                   of the note you read (from a JSON read_note, write_note or edit_note
                   result). The
                   note is replaced only while it is still that revision; otherwise the
                   tool reports a revision conflict with the current checksum and changes
                   nothing. Omit it to replace the note unconditionally.
        output_format: "text" returns a markdown summary. "json" returns
                       machine-readable metadata. A refused write (note exists, revision
                       conflict, moved target, disallowed directory) is a tool error; in
                       JSON mode its message is the payload with action: "conflict" and an
                       error code.
        context: Optional FastMCP context for performance caching.

    Returns:
        A markdown formatted summary of the semantic content, including:
        - Creation/update status with project name
        - File path and checksum
        - Observation counts by category
        - Relation counts (resolved/unresolved)
        - Tags if present
        - Closest existing notes when the note was created and the project's server has
          semantic search enabled, so an accidental near-duplicate can be merged instead of kept
        - Session tracking metadata for project awareness

    Examples:
        # Create a simple note (uses default project automatically)
        write_note(
            project="my-research",
            title="Meeting Notes",
            directory="meetings",
            content="# Weekly Standup\\n\\n- [decision] Use SQLite for storage #tech"
        )

        # Create a note with tags and note type
        write_note(
            project="work-project",
            title="API Design",
            directory="specs",
            content="# REST API Specification\\n\\n- implements [[Authentication]]",
            tags=["api", "design"],
            note_type="guide"
        )

        # Overwrite an existing note explicitly
        write_note(
            project="my-research",
            title="Meeting Notes",
            directory="meetings",
            content="# Weekly Standup\\n\\n- [decision] Use PostgreSQL instead #tech",
            overwrite=True
        )

        # Overwrite only if nobody changed the note since you read it
        write_note(
            project="my-research",
            title="Meeting Notes",
            directory="meetings",
            content="# Weekly Standup\\n\\n- [decision] Keep PostgreSQL #tech",
            overwrite=True,
            expected_checksum="<checksum from the previous JSON result>",
        )

        # Create a schema note with custom frontmatter via metadata
        write_note(
            title="Person",
            directory="schemas",
            note_type="schema",
            content="# Person\\n\\nSchema for person entities.",
            metadata={
                "entity": "person",
                "version": 1,
                "schema": {"name": "string", "role?": "string"},
                "settings": {"validation": "warn"},
            },
        )

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        SecurityError: If directory path attempts path traversal
    """
    # Resolve overwrite flag: explicit parameter > config default
    # Trigger: caller omitted the parameter (None)
    # Why: lets users set a global default without breaking per-call overrides
    effective_overwrite = (
        overwrite if overwrite is not None else ConfigManager().config.write_note_overwrite_default
    )
    # A create has no prior revision, so a checksum only conditions a replacement.
    if expected_checksum is not None and not effective_overwrite:
        raise ValueError("expected_checksum requires overwrite=True")
    project = _compose_workspace_project_route(
        workspace=workspace,
        project=project,
        project_id=project_id,
    )

    with logfire.span(
        "mcp.tool.write_note",
        entrypoint="mcp",
        tool_name="write_note",
        requested_project=project,
        requested_project_id=project_id,
        note_type=note_type,
        overwrite=effective_overwrite,
        conditional_overwrite=expected_checksum is not None,
        output_format=output_format,
    ):
        async with get_project_client(project, context=context, project_id=project_id) as (
            client,
            active_project,
        ):
            logger.debug(
                f"MCP tool call tool=write_note project={active_project.name} directory={directory}, title={title}, tags={tags}"
            )

            # Normalize "/" to empty string for root directory (must happen before validation)
            if directory == "/":
                directory = ""

            # Validate directory path to prevent path traversal attacks
            project_path = active_project.home
            if directory and not validate_project_path(directory, project_path):
                logger.warning(
                    "Attempted path traversal attack blocked",
                    directory=directory,
                    project=active_project.name,
                )
                _raise_write_refusal(
                    output_format,
                    {
                        "title": title,
                        "permalink": None,
                        "file_path": None,
                        "checksum": None,
                        "action": "created",
                        "error": "SECURITY_VALIDATION_ERROR",
                    },
                    f"# Error\n\nDirectory path '{directory}' is not allowed - paths must stay "
                    "within project boundaries",
                )

            # Process tags using the helper function
            tag_list = parse_tags(tags)

            # Build entity_metadata from optional metadata, then explicit tags on top
            # Order matters: explicit tags parameter takes precedence over metadata["tags"]
            entity_metadata = {}
            if metadata:
                entity_metadata.update(metadata)
            if tag_list:
                entity_metadata["tags"] = tag_list

            entity = Entity(
                title=title,
                directory=directory,
                note_type=note_type,
                content_type="text/markdown",
                content=content,
                entity_metadata=entity_metadata or None,
            )

            # Import here to avoid circular import
            from basic_memory.mcp.clients import KnowledgeClient, SearchClient

            # Use typed KnowledgeClient for API calls
            knowledge_client = KnowledgeClient(client, active_project.external_id)

            # The API owns path identity and overwrite policy; expected outcomes stay
            # typed all the way here, so presentation never has to parse an HTTP error.
            outcome = await knowledge_client.write_note(
                entity,
                overwrite=effective_overwrite,
                expected_checksum=expected_checksum,
            )
            match outcome:
                case NoteCreated(entity=result):
                    action = "Created"
                case NoteUpdated(entity=result):
                    action = "Updated"
                case NoteAlreadyExists() as existing:
                    # Trigger: a note already owns the requested path.
                    # Why: the permalink computed from this request can name another
                    #      note. After a move, the moved note keeps the path's original
                    #      permalink and the note now at the path has a suffixed one, so
                    #      the computed value would steer edit_note to the wrong note.
                    # Outcome: the refusal names the note the API found at the path, by
                    #          its stored permalink, or by file path when the API could
                    #          not name it (an older server, or a lost create race).
                    existing_permalink = existing.permalink
                    conflict_workspace = current_workspace_permalink_context()
                    if existing_permalink and conflict_workspace is not None:
                        existing_permalink = build_qualified_permalink_reference(
                            active_project.permalink,
                            existing_permalink,
                            workspace_permalink=conflict_workspace.workspace_slug,
                        )
                    _raise_write_refusal(
                        output_format,
                        {
                            "title": title,
                            "permalink": existing_permalink,
                            "file_path": existing.file_path,
                            "external_id": existing.external_id,
                            "checksum": None,
                            "action": "conflict",
                            "error": "NOTE_ALREADY_EXISTS",
                        },
                        _format_overwrite_error(
                            title, existing_permalink or existing.file_path, active_project.name
                        ),
                    )
                case NoteTargetMoved() as moved:
                    _raise_write_refusal(
                        output_format,
                        {
                            "title": moved.title,
                            "permalink": moved.permalink,
                            "file_path": moved.file_path,
                            "checksum": None,
                            "action": "conflict",
                            "error": "NOTE_PATH_CONFLICT",
                        },
                        "# Error: Note is at a different path\n\n"
                        f"The requested note is now at `{moved.file_path}`. "
                        f"Read or edit it using `{moved.external_id}`. "
                        "Use overwrite=False to create a separate note at the requested path.",
                    )
                case NoteLocked(message=message):
                    raise ToolError(message)
                case NoteRevisionConflict(db_checksum=current_checksum) as conflict:
                    _raise_write_refusal(
                        output_format,
                        {
                            "title": title,
                            "permalink": entity.permalink,
                            "file_path": conflict.file_path if current_checksum else None,
                            "checksum": current_checksum,
                            "action": "conflict",
                            "error": "NOTE_REVISION_CONFLICT",
                        },
                        _format_revision_conflict(
                            title, conflict.file_path, current_checksum, active_project.name
                        ),
                    )
                case _:
                    assert_never(outcome)
            # --- Similar-note advisory ---
            # Trigger: the note was created (not updated).
            # Why: agents writing across sessions know the topic but not whether a note on
            #      it already exists (#1259). A near-duplicate and a merely related note are
            #      not separable by similarity score, so nothing is overwritten on the
            #      caller's behalf; the closest notes are surfaced and the call is theirs.
            #      Whether semantic search is available is the routed server's call, not
            #      this process's config: a local MCP can route a write to a cloud project
            #      whose vectors are on while the local install's are off, so the probe is
            #      always attempted and the server's refusal suppresses it.
            # Outcome: the summary gains a "Similar existing notes" section when the index
            #          has neighbors. The write itself is already complete either way.
            similar_notes: list[SimilarNote] = []
            if action == "Created":
                try:
                    similar_notes = await _find_similar_notes(
                        SearchClient(client, active_project.external_id),
                        title=title,
                        content=content,
                        exclude_file_path=result.file_path,
                        exclude_permalink=result.permalink,
                        min_similarity=similar_notes_min_similarity(ConfigManager().config),
                    )
                except ToolError as probe_error:
                    # ToolError is what the search client raises when the API refuses or
                    # cannot complete the probe: semantic search disabled server-side,
                    # missing embedding dependencies, a 5xx, a transport failure. The note
                    # is already on disk, so those expected failures must not be reported
                    # as a failed write. Anything else is a defect in this code path and
                    # stays visible rather than degrading to an empty advisory.
                    probe_log = (
                        logger.debug if _probe_declined_by_server(probe_error) else logger.warning
                    )
                    probe_log(
                        f"write_note similar-note probe failed project={active_project.name} "
                        f"permalink={result.permalink}: {probe_error}"
                    )

            response_permalink = result.permalink
            workspace_context = current_workspace_permalink_context()
            if workspace_context is not None:
                if response_permalink:
                    response_permalink = build_qualified_permalink_reference(
                        active_project.permalink,
                        response_permalink,
                        workspace_permalink=workspace_context.workspace_slug,
                    )
                similar_notes = [
                    dataclasses.replace(
                        note,
                        permalink=build_qualified_permalink_reference(
                            active_project.permalink,
                            note.permalink,
                            workspace_permalink=workspace_context.workspace_slug,
                        ),
                    )
                    if note.permalink
                    else note
                    for note in similar_notes
                ]

            # Report the accepted revision's checksum. It is recorded
            # synchronously at accept time, and it is the value checksum-guarded
            # edits compare base_checksum against. file_checksum is filled in
            # later by deferred materialization and can drift if the file is
            # edited outside Basic Memory, so it is not what a caller should
            # echo back (#1586).
            checksum = result.db_checksum

            summary = [
                f"# {action} note",
                f"project: {active_project.name}",
                f"file_path: {result.file_path}",
                f"permalink: {response_permalink}",
                f"checksum: {checksum[:8] if checksum else 'unknown'}",
            ]

            # Count observations by category
            categories = {}
            if result.observations:
                for obs in result.observations:
                    categories[obs.category] = categories.get(obs.category, 0) + 1

                summary.append("\n## Observations")
                for category, count in sorted(categories.items()):
                    summary.append(f"- {category}: {count}")

            # Count resolved/unresolved relations
            unresolved = 0
            resolved = 0
            if result.relations:
                unresolved = sum(1 for r in result.relations if not r.to_id)
                resolved = len(result.relations) - unresolved

                summary.append("\n## Relations")
                summary.append(f"- Resolved: {resolved}")
                if unresolved:
                    summary.append(f"- Unresolved: {unresolved}")
                    summary.append(
                        "\nNote: Unresolved relations point to entities that don't exist yet."
                    )
                    summary.append(
                        "They will be automatically resolved when target entities are created or during sync operations."
                    )

            if tag_list:
                summary.append(f"\n## Tags\n- {', '.join(tag_list)}")

            if similar_notes:
                summary.append(
                    _format_similar_notes_section(similar_notes, new_permalink=response_permalink)
                )

            # Log the response with structured data
            logger.debug(
                f"MCP tool response: tool=write_note project={active_project.name} action={action} permalink={response_permalink} observations_count={len(result.observations)} relations_count={len(result.relations)} resolved_relations={resolved} unresolved_relations={unresolved} similar_notes_count={len(similar_notes)}"
            )
            if output_format == "json":
                return {
                    "title": result.title,
                    "permalink": response_permalink,
                    "file_path": result.file_path,
                    "checksum": checksum,
                    "action": action.lower(),
                    "similar_notes": [dataclasses.asdict(note) for note in similar_notes],
                }

            summary_result = "\n".join(summary)
            return add_project_metadata(summary_result, active_project.name)


def _raise_write_refusal(output_format: str, payload: dict[str, Any], text: str) -> NoReturn:
    """Report a refused write as a tool error, keeping the guidance for the caller.

    Every refusal here happens before anything is written. A returned "# Error" string
    reads as success to MCP clients that check isError; raising makes the result an
    error while the message still carries the same help (or the JSON payload).
    """
    raise ToolError(json.dumps(payload) if output_format == "json" else text)


def _format_revision_conflict(
    title: str, file_path: str, current_checksum: str | None, project_name: str
) -> str:
    """Explain a refused conditional overwrite and the ways forward."""
    if current_checksum is None:
        return textwrap.dedent(f"""\
            # Error: Note revision conflict

            **"{title}"** no longer exists at `{file_path}`, so nothing was replaced.
            Read the project again before writing, or drop expected_checksum to create
            a new note at this path.

            Project: {project_name}""")
    return textwrap.dedent(f"""\
        # Error: Note revision conflict

        **"{title}"** at `{file_path}` changed since you read it, so nothing was replaced.
        Current checksum: `{current_checksum}`

        Read the note again and retry with expected_checksum="{current_checksum}", or drop
        expected_checksum to replace it unconditionally.

        Project: {project_name}""")


def _format_overwrite_error(title: str, identifier: str, project_name: str) -> str:
    """Format a helpful error when write_note is blocked by the overwrite guard.

    ``identifier`` names the note that owns the path: its permalink, or its file path.
    """
    return textwrap.dedent(f"""\
        # Error: Note already exists

        **"{title}"** already exists (`{identifier}`).

        `write_note` does not overwrite by default. Choose an option:

        | Goal | Action |
        |------|--------|
        | Append content | `edit_note("{identifier}", operation="append", content="...")` |
        | Prepend content | `edit_note("{identifier}", operation="prepend", content="...")` |
        | Replace a section | `edit_note("{identifier}", operation="replace_section", section="...", content="...")` |
        | Full replace | `write_note(title="{title}", content="...", directory="...", overwrite=True)` |
        | Inspect first | `read_note("{identifier}")` |

        Project: {project_name}""")
