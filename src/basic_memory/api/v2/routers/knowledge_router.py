"""V2 Knowledge Router - External ID-based entity operations.

This router provides external_id (UUID) based CRUD operations for entities,
using stable string UUIDs that won't change with file moves or database migrations.

Key improvements:
- Stable external UUIDs that won't change with file moves or renames
- Better API ergonomics with consistent string identifiers
- Direct database lookups via unique indexed column
- Simplified caching strategies
"""

from collections.abc import Mapping
from hashlib import sha256
import os
import pathlib
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, Path, status
from loguru import logger

import logfire
from basic_memory import db
from basic_memory.cloud import DirectoryDeleteServiceError, NoteContentMutationServiceError
from basic_memory.ignore_utils import (
    IGNORED_PATH_REJECTION_DETAIL,
    load_gitignore_patterns,
    should_ignore_path,
)
from basic_memory.deps import (
    EntityServiceV2ExternalDep,
    FileServiceV2ExternalDep,
    SearchServiceV2ExternalDep,
    LinkResolverV2ExternalDep,
    NoteContentMaterializationProviderDep,
    NoteContentMutationServiceDep,
    NoteContentQueryServiceDep,
    DirectoryDeleteServiceDep,
    ProjectRepositoryDep,
    ProjectConfigV2ExternalDep,
    AppConfigDep,
    EntityRepositoryV2ExternalDep,
    RelationRepositoryV2ExternalDep,
    ProjectExternalIdPathDep,
    IndexFileExecutorV2ExternalDep,
    EntityVectorSyncSchedulerDep,
    RelationResolutionSchedulerDep,
    RuntimeTenantIdDep,
    SessionDep,
    SessionMakerDep,
)
from basic_memory.runtime import (
    runtime_note_content_payload_as_dict,
    runtime_note_content_payload_as_json_bytes,
)
from basic_memory.schemas import DeleteEntitiesResponse
from basic_memory.schemas.base import Entity
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.schemas.v2 import (
    EntityResolveRequest,
    EntityResolveResponse,
    EntityResponseV2,
    GraphEdge,
    GraphNode,
    GraphResponse,
    MoveEntityRequestV2,
    MoveDirectoryRequestV2,
    DeleteDirectoryRequestV2,
    OrphanEntitiesResponse,
    IndexFileRequest,
)
from basic_memory.schemas.response import DirectoryMoveResult
from basic_memory.utils import validate_project_path

router = APIRouter(prefix="/knowledge", tags=["knowledge-v2"])


def _schedule_post_write_followups(
    *,
    vector_sync_scheduler,
    relation_resolution_scheduler,
    app_config,
    entity_id: int,
    project_id: int,
) -> None:
    """Schedule out-of-band follow-ups after an accepted note mutation.

    Vector sync only runs when semantic search is enabled. Relation resolution
    always runs so a newly written note back-resolves inbound forward references
    that name it — parity with the watcher's relation repair, which the inline
    write path does not otherwise trigger (#1015). Both are no-ops in test mode.

    CLOUD/LOCAL PARITY (do not break): every follow-up here is a core *router
    scheduler* that the runtime overrides via dependency injection — local runs
    it in-process (debounced), cloud overrides each to enqueue a PGQ job. Cloud
    must override BOTH get_entity_vector_sync_scheduler AND
    get_relation_resolution_scheduler; if a new follow-up scheduler is added
    here, cloud needs a matching override or it will run the local in-process
    work on the cloud API server. See basic_memory_cloud api/deps/cloud_overrides.
    """
    if app_config.semantic_search_enabled:
        vector_sync_scheduler.schedule_entity_vector_sync(
            entity_id=entity_id,
            project_id=project_id,
        )
    relation_resolution_scheduler.schedule_relation_resolution(project_id=project_id)


def entity_response_from_note_content_payload(payload) -> EntityResponseV2:
    """Serialize an accepted-note payload through the v2 entity response model."""
    return EntityResponseV2.model_validate(runtime_note_content_payload_as_dict(payload))


def delete_response_from_note_content_payload(payload) -> DeleteEntitiesResponse:
    """Serialize an accepted-note delete payload through the existing delete model."""
    return DeleteEntitiesResponse.model_validate(runtime_note_content_payload_as_dict(payload))


def etag_for_response_body(body: bytes) -> str:
    """Return a stable HTTP ETag for a runtime JSON response body."""
    return f'"{sha256(body).hexdigest()}"'


def runtime_json_response(
    *,
    status_code: int,
    payload: Mapping[str, object],
) -> Response:
    """Serialize a runtime payload without dropping adapter-specific fields."""
    response_body = runtime_note_content_payload_as_json_bytes(payload)
    return Response(
        status_code=status_code,
        headers={
            "content-type": "application/json",
            "etag": etag_for_response_body(response_body),
        },
        content=response_body,
    )


## Graph endpoint


@router.get("/graph", response_model=GraphResponse)
async def get_graph(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    session: SessionDep,
) -> GraphResponse:
    """Return all entities and resolved relations for knowledge graph visualization.

    Returns a flat node/edge structure optimized for rendering with graph libraries.
    Only includes resolved relations (where to_id is not null).
    """
    with logfire.span(
        "api.request.knowledge.get_graph",
        entrypoint="api",
        domain="knowledge",
        action="get_graph",
    ):
        logger.info("API v2 request: get_graph")

        # Fetch all entities for this project
        entities = await entity_repository.find_all(session, use_load_options=False)
        nodes = [
            GraphNode(
                external_id=entity.external_id,
                title=entity.title,
                note_type=entity.note_type,
                file_path=entity.file_path,
            )
            for entity in entities
        ]

        # Fetch all resolved relations (to_id is not null) with eager-loaded entities
        relations = await relation_repository.find_all(session)
        edges = [
            GraphEdge(
                from_id=relation.from_entity.external_id,
                to_id=relation.to_entity.external_id,
                relation_type=relation.relation_type,
            )
            for relation in relations
            if relation.to_entity is not None
        ]

        logger.info(f"API v2 response: graph with {len(nodes)} nodes and {len(edges)} edges")
        return GraphResponse(nodes=nodes, edges=edges)


## Orphan entities endpoint


@router.get("/orphans", response_model=OrphanEntitiesResponse)
async def get_orphan_entities(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    session: SessionDep,
) -> OrphanEntitiesResponse:
    """Return entities that have no incoming or outgoing relations."""
    with logfire.span(
        "api.request.knowledge.get_orphans",
        entrypoint="api",
        domain="knowledge",
        action="get_orphans",
    ):
        logger.info("API v2 request: get_orphan_entities")

        entities = await entity_repository.find_without_relations(session)
        nodes = [
            GraphNode(
                external_id=entity.external_id,
                title=entity.title,
                note_type=entity.note_type,
                file_path=entity.file_path,
            )
            for entity in entities
        ]

        logger.info(f"API v2 response: {len(nodes)} orphan entities")
        return OrphanEntitiesResponse(entities=nodes, total=len(nodes))


## Resolution endpoint


@router.post("/resolve", response_model=EntityResolveResponse)
async def resolve_identifier(
    project_id: ProjectExternalIdPathDep,
    data: EntityResolveRequest,
    link_resolver: LinkResolverV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    project_repository: ProjectRepositoryDep,
    session: SessionDep,
) -> EntityResolveResponse:
    """Resolve a string identifier (external_id, permalink, title, or path) to entity info.

    This endpoint provides a bridge between v1-style identifiers and v2 external_ids.
    Use this to convert existing references to the new UUID-based format.

    Args:
        data: Request containing the identifier to resolve

    Returns:
        Entity external_id and metadata about how it was resolved

    Raises:
        HTTPException: 404 if identifier cannot be resolved

    Example:
        POST /v2/{project_id}/knowledge/resolve
        {"identifier": "specs/search"}

        Returns:
        {
            "external_id": "550e8400-e29b-41d4-a716-446655440000",
            "entity_id": 123,
            "project_external_id": "4b9b7a10-7a63-48d2-ae3f-0d6a2c69313f",
            "permalink": "specs/search",
            "file_path": "specs/search.md",
            "title": "Search Specification",
            "resolution_method": "permalink"
        }
    """
    with logfire.span(
        "api.request.knowledge.resolve_entity",
        entrypoint="api",
        domain="knowledge",
        action="resolve_entity",
    ):
        logger.info(f"API v2 request: resolve_identifier for '{data.identifier}'")

        entity = await entity_repository.get_by_external_id(session, data.identifier)
        resolution_method = "external_id" if entity else "search"

        if not entity:
            entity = await link_resolver.resolve_link(
                data.identifier,
                source_path=data.source_path,
                strict=data.strict,
                session=session,
            )
            if entity:
                if entity.permalink == data.identifier:
                    resolution_method = "permalink"
                elif entity.title == data.identifier:
                    resolution_method = "title"
                elif entity.file_path == data.identifier:
                    resolution_method = "path"
                else:
                    resolution_method = "search"

        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity not found: '{data.identifier}'")

        owner_project = await project_repository.get_by_id(session, entity.project_id)
        if not owner_project:  # pragma: no cover
            raise HTTPException(
                status_code=500,
                detail="Resolved entity references an unknown project",
            )

        result = EntityResolveResponse(
            external_id=entity.external_id,
            entity_id=entity.id,
            project_external_id=owner_project.external_id,
            permalink=entity.permalink,
            file_path=entity.file_path,
            title=entity.title,
            resolution_method=resolution_method,
        )

        logger.debug(
            f"API v2 response: resolved '{data.identifier}' to external_id={result.external_id} via {resolution_method}"
        )

        return result


## Single-file indexing endpoint


def _canonical_file_path(home: pathlib.Path, segments: list[str]) -> str | None:
    """Resolve the actual on-disk casing of a file path under the project home.

    Trigger: case-insensitive filesystems (macOS/Windows) pass existence checks for
        wrong-cased paths like 'notes/Disk-Note.md' when the file is 'notes/disk-note.md'.
    Why: indexing the caller-supplied casing misses the existing DB row keyed by the
        on-disk path and inserts a duplicate entity under the wrong-cased path.
    Outcome: each segment is matched against real directory entries — exact name first
        (so distinct case-variant files on case-sensitive filesystems stay distinct),
        then a unique case-insensitive match. Returns None when any segment cannot be
        matched to exactly one entry, including missing files. Traversal stops at the
        project boundary: a directory whose resolved path escapes the project home is
        never scanned.
    """
    resolved_home = home.resolve()
    current = home
    canonical_segments: list[str] = []
    for segment in segments:
        # Trigger: a previously matched segment may be a symlink whose target lies
        #     outside the project root (e.g. wrong-cased 'LINK' matched the on-disk
        #     'link' -> /tmp/outside on a case-sensitive filesystem).
        # Why: os.scandir follows symlinked directories, so continuing would read
        #     directory contents outside the project boundary even though the
        #     post-canonicalization containment check rejects the request later.
        # Outcome: bail before scanning the moment resolution escapes the home.
        if not current.resolve().is_relative_to(resolved_home):
            return None
        try:
            with os.scandir(current) as entries_iter:
                entries = [entry.name for entry in entries_iter]
        except OSError:
            # A parent segment resolved to a non-directory (or vanished): no canonical
            # path exists for the remaining segments.
            return None
        if segment in entries:
            matched = segment
        else:
            matches = [entry for entry in entries if entry.lower() == segment.lower()]
            if len(matches) != 1:
                return None
            matched = matches[0]
        canonical_segments.append(matched)
        current = current / matched
    return "/".join(canonical_segments)


@router.post("/index-file", response_model=EntityResponseV2)
async def index_file(
    data: IndexFileRequest,
    project_id: ProjectExternalIdPathDep,
    file_service: FileServiceV2ExternalDep,
    file_indexer: IndexFileExecutorV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    project_config: ProjectConfigV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    app_config: AppConfigDep,
    session_maker: SessionMakerDep,
) -> EntityResponseV2:
    """Index a single markdown file that exists on disk but is not indexed yet.

    Recovery path for files written directly to disk before the watcher indexed
    them (#581): callers such as edit_note can index the exact file and retry
    identifier resolution without running a full project sync.

    Args:
        data: Request containing the markdown file path relative to project root

    Returns:
        The indexed entity

    Raises:
        HTTPException: 400 if the path escapes the project root, contains
            non-normalized segments, matches the project ignore rules, or is
            not markdown, 404 if the file does not exist on disk
    """
    with logfire.span(
        "api.request.knowledge.index_file",
        entrypoint="api",
        domain="knowledge",
        action="index_file",
    ):
        logger.info(f"API v2 request: index_file file_path='{data.file_path}'")

        if not validate_project_path(data.file_path, project_config.home):
            raise HTTPException(
                status_code=400,
                detail=f"File path '{data.file_path}' is not allowed - "
                "paths must stay within project boundaries",
            )

        # Trigger: segments like './' or '//' survive the traversal check above
        # Why: a non-normalized path would index under a non-canonical DB key
        # Outcome: reject fail-fast instead of guessing the canonical form
        segments = data.file_path.replace("\\", "/").split("/")
        if any(segment in ("", ".") for segment in segments):
            raise HTTPException(
                status_code=400,
                detail=f"File path '{data.file_path}' is not normalized - "
                "segments like './' or '//' are not allowed",
            )

        # Canonicalize to the actual on-disk casing so the DB lookup below hits the
        # row keyed by the real path instead of inserting a wrong-cased duplicate.
        file_path = _canonical_file_path(project_config.home, segments)
        if file_path is None:
            raise HTTPException(
                status_code=404, detail=f"File not found on disk: '{data.file_path}'"
            )
        # Trigger: canonicalization rewrote a segment to its on-disk form, and that
        #     segment may be a symlink. The pre-check above validated the ORIGINAL
        #     request path — on a case-sensitive filesystem 'LINK/secret.md' does not
        #     exist, so resolve() cannot follow the real 'link' symlink and the check
        #     passes even when 'link' points outside the project root.
        # Why: indexing through an escaping symlink would read and index content
        #     outside the project boundary — and even an is_file() existence probe on
        #     the joined path would follow the symlink and stat its target, so
        #     containment must hold BEFORE any filesystem probe that follows symlinks.
        #     Path.resolve() only walks symlink names (readlink); it never opens or
        #     stats the final target, so it is safe to run pre-containment.
        # Outcome: the canonical path is re-validated and the fully-resolved absolute
        #     target must stay inside the resolved project home; escapes get a 400
        #     before the file-existence probe below ever touches the target.
        resolved_target = (project_config.home / file_path).resolve()
        if not validate_project_path(file_path, project_config.home) or not (
            resolved_target.is_relative_to(project_config.home.resolve())
        ):
            raise HTTPException(
                status_code=400,
                detail=f"File path '{data.file_path}' is not allowed - "
                "paths must stay within project boundaries",
            )
        # Containment holds, so probing the resolved target cannot leave the project.
        if not resolved_target.is_file():
            raise HTTPException(
                status_code=404, detail=f"File not found on disk: '{data.file_path}'"
            )
        # Trigger: the canonical path matches the .bmignore / project .gitignore rules
        # Why: scan and watch flows filter ignored files before they ever reach the
        #      indexer; indexing one here would bypass the ignored-file contract and
        #      make hidden or gitignored content searchable
        # Outcome: the same should_ignore_path() rules apply to single-file indexing
        ignore_patterns = load_gitignore_patterns(project_config.home)
        if should_ignore_path(
            project_config.home / file_path, project_config.home, ignore_patterns
        ):
            raise HTTPException(
                status_code=400,
                detail=f"File path '{data.file_path}' {IGNORED_PATH_REJECTION_DETAIL} "
                "and cannot be indexed",
            )
        if not file_service.is_markdown(file_path):
            raise HTTPException(
                status_code=400,
                detail=f"Only markdown files can be indexed: '{data.file_path}'",
            )

        indexed = await file_indexer.index_file(file_path, source="api-index-file")
        async with db.scoped_session(session_maker) as session:
            entity = await entity_repository.get_by_id(session, indexed.entity_id)
        if entity is None:  # pragma: no cover
            raise HTTPException(
                status_code=500,
                detail=f"Indexed entity not found after indexing: '{data.file_path}'",
            )

        # Trigger: semantic search is enabled and the entity index was just refreshed
        # Why: project indexing refreshes embedding vectors after changed files are
        #      indexed; without the single-entity equivalent, a note recovered via
        #      index-file stays missing or stale in semantic search until later work
        # Outcome: vectors refresh synchronously before the response returns,
        #          mirroring project indexing instead of the out-of-band scheduler
        if app_config.semantic_search_enabled:
            await search_service.sync_entity_vectors_batch([entity.id])

        result = EntityResponseV2.model_validate(entity)
        logger.info(
            f"API v2 response: index_file file_path='{file_path}' external_id={result.external_id}"
        )
        return result


## Read endpoints


@router.get("/entities/{entity_id}", response_model=EntityResponseV2)
async def get_entity_by_id(
    project_id: ProjectExternalIdPathDep,
    project_repository: ProjectRepositoryDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    note_content_query_service: NoteContentQueryServiceDep,
    session: SessionDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Get an entity by its external ID (UUID).

    This is the primary entity retrieval method in v2, using stable UUID
    identifiers that won't change with file moves.

    Args:
        entity_id: External ID (UUID string)

    Returns:
        Complete entity with observations and relations

    Raises:
        HTTPException: 404 if entity not found
    """
    with logfire.span(
        "api.request.knowledge.get_entity",
        entrypoint="api",
        domain="knowledge",
        action="get_entity",
    ):
        logger.info(f"API v2 request: get_entity_by_id entity_id={entity_id}")

        project = await project_repository.get_by_id(session, project_id)
        if project is None:  # pragma: no cover
            raise HTTPException(status_code=404, detail=f"Project with ID {project_id} not found")

        note_payload = await note_content_query_service.get_note_entity_payload_with_read_repair(
            project_external_id=project.external_id,
            entity_external_id=entity_id,
            session=session,
        )
        if note_payload is not None:
            result = entity_response_from_note_content_payload(note_payload)
            logger.info(f"API v2 response: external_id={entity_id}, title='{result.title}'")
            return result

        entity = await entity_repository.get_by_external_id(session, entity_id)
        if not entity:
            raise HTTPException(
                status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
            )

        result = EntityResponseV2.model_validate(entity)
        logger.info(f"API v2 response: external_id={entity_id}, title='{result.title}'")

        return result


## Create endpoints


@router.post("/entities", response_model=EntityResponseV2, status_code=status.HTTP_202_ACCEPTED)
async def create_entity(
    project_id: ProjectExternalIdPathDep,
    project_external_id: Annotated[
        str, Path(alias="project_id", description="Project external UUID")
    ],
    data: Entity,
    note_content_mutation_service: NoteContentMutationServiceDep,
    note_content_materialization_provider: NoteContentMaterializationProviderDep,
    vector_sync_scheduler: EntityVectorSyncSchedulerDep,
    relation_resolution_scheduler: RelationResolutionSchedulerDep,
    app_config: AppConfigDep,
) -> EntityResponseV2:
    """Create a new entity.

    Args:
        data: Entity data to create

    Returns:
        Created entity with generated external_id (UUID) and file content
    """
    with logfire.span(
        "api.request.knowledge.create_entity",
        entrypoint="api",
        domain="knowledge",
        action="create_entity",
    ):
        logger.info(
            "API v2 request", endpoint="create_entity", note_type=data.note_type, title=data.title
        )

        try:
            accepted = await note_content_mutation_service.create_note(
                project_external_id=project_external_id,
                data=data,
                user_profile_id=None,
                source="api",
            )
        except NoteContentMutationServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error

        accepted = await note_content_materialization_provider.materialize_write_change(accepted)
        result = entity_response_from_note_content_payload(accepted.payload)
        _schedule_post_write_followups(
            vector_sync_scheduler=vector_sync_scheduler,
            relation_resolution_scheduler=relation_resolution_scheduler,
            app_config=app_config,
            entity_id=result.id,
            project_id=project_id,
        )

        logger.info(
            f"API v2 response: endpoint='create_entity' external_id={result.external_id}, title={result.title}, permalink={result.permalink}, status_code=202"
        )
        return result


## Update endpoints


@router.put(
    "/entities/{entity_id}",
    response_model=EntityResponseV2,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_entity_by_id(
    data: Entity,
    response: Response,
    project_id: ProjectExternalIdPathDep,
    project_external_id: Annotated[
        str, Path(alias="project_id", description="Project external UUID")
    ],
    note_content_mutation_service: NoteContentMutationServiceDep,
    note_content_materialization_provider: NoteContentMaterializationProviderDep,
    vector_sync_scheduler: EntityVectorSyncSchedulerDep,
    relation_resolution_scheduler: RelationResolutionSchedulerDep,
    app_config: AppConfigDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Update an entity by external ID.

    If the entity doesn't exist, it will be created (upsert behavior).

    Args:
        entity_id: External ID (UUID string)
        data: Updated entity data

    Returns:
        Updated entity with file content
    """
    with logfire.span(
        "api.request.knowledge.update_entity",
        entrypoint="api",
        domain="knowledge",
        action="update_entity",
    ):
        logger.info(f"API v2 request: update_entity_by_id entity_id={entity_id}")

        try:
            accepted = await note_content_mutation_service.update_note(
                project_external_id=project_external_id,
                entity_external_id=entity_id,
                data=data,
                user_profile_id=None,
                source="api",
            )
        except NoteContentMutationServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error

        accepted = await note_content_materialization_provider.materialize_write_change(accepted)
        response.status_code = status.HTTP_202_ACCEPTED
        result = entity_response_from_note_content_payload(accepted.payload)
        _schedule_post_write_followups(
            vector_sync_scheduler=vector_sync_scheduler,
            relation_resolution_scheduler=relation_resolution_scheduler,
            app_config=app_config,
            entity_id=result.id,
            project_id=project_id,
        )

        logger.info(f"API v2 response: external_id={entity_id}, status_code={response.status_code}")
        return result


@router.patch(
    "/entities/{entity_id}",
    response_model=EntityResponseV2,
    status_code=status.HTTP_202_ACCEPTED,
)
async def edit_entity_by_id(
    data: EditEntityRequest,
    project_id: ProjectExternalIdPathDep,
    project_external_id: Annotated[
        str, Path(alias="project_id", description="Project external UUID")
    ],
    note_content_mutation_service: NoteContentMutationServiceDep,
    note_content_materialization_provider: NoteContentMaterializationProviderDep,
    vector_sync_scheduler: EntityVectorSyncSchedulerDep,
    relation_resolution_scheduler: RelationResolutionSchedulerDep,
    app_config: AppConfigDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Edit an existing entity by external ID using operations like append, prepend, etc.

    Args:
        entity_id: External ID (UUID string)
        data: Edit operation details

    Returns:
        Updated entity with file content

    Raises:
        HTTPException: 404 if entity not found, 400 if edit fails
    """
    with logfire.span(
        "api.request.knowledge.edit_entity",
        entrypoint="api",
        domain="knowledge",
        action="edit_entity",
    ):
        logger.info(
            f"API v2 request: edit_entity_by_id entity_id={entity_id}, operation='{data.operation}'"
        )

        try:
            accepted = await note_content_mutation_service.edit_note(
                project_external_id=project_external_id,
                entity_external_id=entity_id,
                data=data,
                user_profile_id=None,
                source="api",
            )
        except NoteContentMutationServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error

        accepted = await note_content_materialization_provider.materialize_write_change(accepted)
        result = entity_response_from_note_content_payload(accepted.payload)
        _schedule_post_write_followups(
            vector_sync_scheduler=vector_sync_scheduler,
            relation_resolution_scheduler=relation_resolution_scheduler,
            app_config=app_config,
            entity_id=result.id,
            project_id=project_id,
        )

        logger.info(
            f"API v2 response: external_id={entity_id}, operation='{data.operation}', status_code=202"
        )

        return result


## Delete endpoints


@router.delete(
    "/entities/{entity_id}",
    response_model=DeleteEntitiesResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_entity_by_id(
    project_id: ProjectExternalIdPathDep,
    project_external_id: Annotated[
        str, Path(alias="project_id", description="Project external UUID")
    ],
    note_content_mutation_service: NoteContentMutationServiceDep,
    note_content_materialization_provider: NoteContentMaterializationProviderDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> DeleteEntitiesResponse:
    """Delete an entity by external ID.

    Args:
        entity_id: External ID (UUID string)

    Returns:
        Deletion status

    Note: Returns deleted=False if entity doesn't exist (idempotent)
    """
    with logfire.span(
        "api.request.knowledge.delete_entity",
        entrypoint="api",
        domain="knowledge",
        action="delete_entity",
    ):
        logger.info(f"API v2 request: delete_entity_by_id entity_id={entity_id}")

        try:
            accepted = await note_content_mutation_service.delete_note(
                project_external_id=project_external_id,
                entity_external_id=entity_id,
            )
        except NoteContentMutationServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error

        accepted = await note_content_materialization_provider.materialize_delete_change(accepted)
        result = delete_response_from_note_content_payload(accepted.payload)

        logger.info(f"API v2 response: external_id={entity_id}, deleted={result.deleted}")

        return result


## Move endpoint


@router.put(
    "/entities/{entity_id}/move",
    response_model=EntityResponseV2,
    status_code=status.HTTP_202_ACCEPTED,
)
async def move_entity(
    data: MoveEntityRequestV2,
    project_id: ProjectExternalIdPathDep,
    project_external_id: Annotated[
        str, Path(alias="project_id", description="Project external UUID")
    ],
    note_content_mutation_service: NoteContentMutationServiceDep,
    note_content_materialization_provider: NoteContentMaterializationProviderDep,
    app_config: AppConfigDep,
    vector_sync_scheduler: EntityVectorSyncSchedulerDep,
    relation_resolution_scheduler: RelationResolutionSchedulerDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Move an entity to a new file location.

    V2 API uses external_id (UUID) in the URL path for stable references.
    The external_id will remain stable after the move.

    Args:
        project_id: Project external ID from URL path
        entity_id: Entity external ID from URL path (primary identifier)
        data: Move request with destination path only

    Returns:
        Updated entity with new file path
    """
    with logfire.span(
        "api.request.knowledge.move_entity",
        entrypoint="api",
        domain="knowledge",
        action="move_entity",
    ):
        logger.info(
            f"API v2 request: move_entity entity_id={entity_id}, destination='{data.destination_path}'"
        )

        try:
            accepted = await note_content_mutation_service.move_note(
                project_external_id=project_external_id,
                entity_external_id=entity_id,
                destination_path=data.destination_path,
                user_profile_id=None,
                source="api",
            )
        except NoteContentMutationServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error

        accepted = await note_content_materialization_provider.materialize_write_change(accepted)
        result = entity_response_from_note_content_payload(accepted.payload)
        _schedule_post_write_followups(
            vector_sync_scheduler=vector_sync_scheduler,
            relation_resolution_scheduler=relation_resolution_scheduler,
            app_config=app_config,
            entity_id=result.id,
            project_id=project_id,
        )

        logger.info(f"API v2 response: moved external_id={entity_id} to '{data.destination_path}'")

        return result


## Move directory endpoint


@router.post("/move-directory", response_model=DirectoryMoveResult)
async def move_directory(
    data: MoveDirectoryRequestV2,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    project_config: ProjectConfigV2ExternalDep,
    app_config: AppConfigDep,
    search_service: SearchServiceV2ExternalDep,
    vector_sync_scheduler: EntityVectorSyncSchedulerDep,
    relation_resolution_scheduler: RelationResolutionSchedulerDep,
    session_maker: SessionMakerDep,
) -> DirectoryMoveResult:
    """Move all entities in a directory to a new location.

    V2 API uses project external_id in the URL path for stable references.
    Moves all files within a source directory to a destination directory,
    updating database records and optionally updating permalinks.

    Args:
        project_id: Project external ID from URL path
        data: Move request with source and destination directories

    Returns:
        DirectoryMoveResult with counts and details of moved files
    """
    with logfire.span(
        "api.request.knowledge.move_directory",
        entrypoint="api",
        domain="knowledge",
        action="move_directory",
    ):
        logger.info(
            f"API v2 request: move_directory source='{data.source_directory}', destination='{data.destination_directory}'"
        )

        try:
            # Move the directory using the service
            result = await entity_service.move_directory(
                source_directory=data.source_directory,
                destination_directory=data.destination_directory,
                project_config=project_config,
                app_config=app_config,
            )

            # Reindex moved entities
            for file_path in result.moved_files:
                async with db.scoped_session(session_maker) as session:
                    entity = await entity_service.link_resolver.resolve_link(
                        file_path, session=session
                    )
                if entity:
                    await search_service.index_entity(entity)
                    _schedule_post_write_followups(
                        vector_sync_scheduler=vector_sync_scheduler,
                        relation_resolution_scheduler=relation_resolution_scheduler,
                        app_config=app_config,
                        entity_id=entity.id,
                        project_id=project_id,
                    )

            logger.info(
                f"API v2 response: move_directory "
                f"total={result.total_files}, success={result.successful_moves}, failed={result.failed_moves}"
            )
            return result

        except Exception as e:
            logger.error(f"Error moving directory: {e}")
            raise HTTPException(status_code=400, detail=str(e))


## Delete directory endpoint


@router.post("/delete-directory", response_class=Response)
async def delete_directory(
    data: DeleteDirectoryRequestV2,
    runtime_tenant_id: RuntimeTenantIdDep,
    project_external_id: Annotated[
        str, Path(alias="project_id", description="Project external UUID")
    ],
    directory_delete_service: DirectoryDeleteServiceDep,
) -> Response:
    """Delete all entities in a directory.

    V2 API uses project external_id in the URL path for stable references.
    Deletes database records first, then delegates backing-file cleanup to the
    active runtime.

    Args:
        project_id: Project external ID from URL path
        data: Delete request with directory path

    Returns:
        JSON payload with counts and runtime file cleanup status
    """
    with logfire.span(
        "api.request.knowledge.delete_directory",
        entrypoint="api",
        domain="knowledge",
        action="delete_directory",
    ):
        logger.info(f"API v2 request: delete_directory directory='{data.directory}'")

        try:
            status_code, payload = await directory_delete_service.delete_directory(
                tenant_id=runtime_tenant_id,
                project_external_id=project_external_id,
                directory=data.directory,
            )
            logger.info(
                f"API v2 response: delete_directory "
                f"total={payload.get('total_files')}, "
                f"success={payload.get('successful_deletes')}, "
                f"failed={payload.get('failed_deletes')}, "
                f"file_delete_status={payload.get('file_delete_status')}"
            )
            return runtime_json_response(status_code=status_code, payload=payload)

        except DirectoryDeleteServiceError as error:
            logger.error(f"Error deleting directory: {error.detail}")
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error
