"""V2 Knowledge Router - External ID-based entity operations.

This router provides external_id (UUID) based CRUD operations for entities,
using stable string UUIDs that won't change with file moves or database migrations.

Key improvements:
- Stable external UUIDs that won't change with file moves or renames
- Better API ergonomics with consistent string identifiers
- Direct database lookups via unique indexed column
- Simplified caching strategies
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Response, Path, Query
from loguru import logger

from basic_memory import telemetry
from basic_memory.deps import (
    EntityServiceV2ExternalDep,
    SearchServiceV2ExternalDep,
    LinkResolverV2ExternalDep,
    ProjectConfigV2ExternalDep,
    AppConfigDep,
    EntityRepositoryV2ExternalDep,
    RelationRepositoryV2ExternalDep,
    ProjectExternalIdPathDep,
    TaskSchedulerDep,
    FileServiceV2ExternalDep,
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
)
from basic_memory.schemas.response import DirectoryMoveResult, DirectoryDeleteResult

router = APIRouter(prefix="/knowledge", tags=["knowledge-v2"])


def _schedule_vector_sync_if_enabled(
    *,
    task_scheduler,
    app_config,
    entity_id: int,
    project_id: int,
) -> None:
    """Schedule out-of-band vector sync only when semantic search is enabled."""
    if app_config.semantic_search_enabled:
        task_scheduler.schedule(
            "sync_entity_vectors",
            entity_id=entity_id,
            project_id=project_id,
        )


## Graph endpoint


@router.get("/graph", response_model=GraphResponse)
async def get_graph(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
) -> GraphResponse:
    """Return all entities and resolved relations for knowledge graph visualization.

    Returns a flat node/edge structure optimized for rendering with graph libraries.
    Only includes resolved relations (where to_id is not null).
    """
    logger.info("API v2 request: get_graph")

    # Fetch all entities for this project
    entities = await entity_repository.find_all(use_load_options=False)
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
    relations = await relation_repository.find_all()
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


## Resolution endpoint


@router.post("/resolve", response_model=EntityResolveResponse)
async def resolve_identifier(
    project_id: ProjectExternalIdPathDep,
    data: EntityResolveRequest,
    link_resolver: LinkResolverV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
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
            "permalink": "specs/search",
            "file_path": "specs/search.md",
            "title": "Search Specification",
            "resolution_method": "permalink"
        }
    """
    with telemetry.operation(
        "api.request.knowledge.resolve_entity",
        entrypoint="api",
        domain="knowledge",
        action="resolve_entity",
    ):
        logger.info(f"API v2 request: resolve_identifier for '{data.identifier}'")

        with telemetry.scope(
            "api.knowledge.resolve_entity.lookup_entity",
            domain="knowledge",
            action="resolve_entity",
            phase="lookup_entity",
        ):
            entity = await entity_repository.get_by_external_id(data.identifier)
        resolution_method = "external_id" if entity else "search"

        if not entity:
            with telemetry.scope(
                "api.knowledge.resolve_entity.resolve_link",
                domain="knowledge",
                action="resolve_entity",
                phase="resolve_link",
            ):
                entity = await link_resolver.resolve_link(
                    data.identifier, source_path=data.source_path, strict=data.strict
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

        with telemetry.scope(
            "api.knowledge.resolve_entity.shape_response",
            domain="knowledge",
            action="resolve_entity",
            phase="shape_response",
        ):
            result = EntityResolveResponse(
                external_id=entity.external_id,
                entity_id=entity.id,
                permalink=entity.permalink,
                file_path=entity.file_path,
                title=entity.title,
                resolution_method=resolution_method,
            )

        logger.debug(
            f"API v2 response: resolved '{data.identifier}' to external_id={result.external_id} via {resolution_method}"
        )

        return result


## Read endpoints


@router.get("/entities/{entity_id}", response_model=EntityResponseV2)
async def get_entity_by_id(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
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
    with telemetry.operation(
        "api.request.knowledge.get_entity",
        entrypoint="api",
        domain="knowledge",
        action="get_entity",
    ):
        logger.info(f"API v2 request: get_entity_by_id entity_id={entity_id}")

        with telemetry.scope(
            "api.knowledge.get_entity.load_entity",
            domain="knowledge",
            action="get_entity",
            phase="load_entity",
        ):
            entity = await entity_repository.get_by_external_id(entity_id)
        if not entity:
            raise HTTPException(
                status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
            )

        with telemetry.scope(
            "api.knowledge.get_entity.shape_response",
            domain="knowledge",
            action="get_entity",
            phase="shape_response",
        ):
            result = EntityResponseV2.model_validate(entity)
        logger.info(f"API v2 response: external_id={entity_id}, title='{result.title}'")

        return result


## Create endpoints


@router.post("/entities", response_model=EntityResponseV2)
async def create_entity(
    project_id: ProjectExternalIdPathDep,
    data: Entity,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
    file_service: FileServiceV2ExternalDep,
    app_config: AppConfigDep,
    fast: bool = Query(
        True, description="If true, write quickly and defer indexing to background tasks."
    ),
) -> EntityResponseV2:
    """Create a new entity.

    Args:
        data: Entity data to create
        fast: If True, defer indexing to background tasks

    Returns:
        Created entity with generated external_id (UUID) and file content
    """
    with telemetry.operation(
        "api.request.knowledge.create_entity",
        entrypoint="api",
        domain="knowledge",
        action="create_entity",
        fast=fast,
    ):
        logger.info(
            "API v2 request", endpoint="create_entity", note_type=data.note_type, title=data.title
        )

        with telemetry.scope(
            "api.knowledge.create_entity.write_entity",
            domain="knowledge",
            action="create_entity",
            phase="write_entity",
            fast=fast,
        ):
            if fast:
                entity = await entity_service.fast_write_entity(data)
                written_content = None
                search_content = None
            else:
                write_result = await entity_service.create_entity_with_content(data)
                entity = write_result.entity
                written_content = write_result.content
                search_content = write_result.search_content

        if fast:
            with telemetry.scope(
                "api.knowledge.create_entity.enqueue_reindex",
                domain="knowledge",
                action="create_entity",
                phase="enqueue_reindex",
                fast=fast,
            ):
                task_scheduler.schedule(
                    "reindex_entity",
                    entity_id=entity.id,
                    project_id=project_id,
                )
        else:
            with telemetry.scope(
                "api.knowledge.create_entity.search_index",
                domain="knowledge",
                action="create_entity",
                phase="search_index",
            ):
                await search_service.index_entity(entity, content=search_content)
            with telemetry.scope(
                "api.knowledge.create_entity.vector_sync",
                domain="knowledge",
                action="create_entity",
                phase="vector_sync",
            ):
                _schedule_vector_sync_if_enabled(
                    task_scheduler=task_scheduler,
                    app_config=app_config,
                    entity_id=entity.id,
                    project_id=project_id,
                )

        result = EntityResponseV2.model_validate(entity)
        if fast:
            result = result.model_copy(update={"observations": [], "relations": []})

        with telemetry.scope(
            "api.knowledge.create_entity.read_content",
            domain="knowledge",
            action="create_entity",
            phase="read_content",
            source="file" if fast else "memory",
        ):
            if fast:
                content = await file_service.read_file_content(entity.file_path)
            else:
                # Non-fast writes already captured the markdown in memory. Reuse it here
                # instead of re-reading the file; format_on_save is the one config that can
                # still make the persisted file diverge because write_file only returns a checksum.
                content = written_content
        result = result.model_copy(update={"content": content})

        logger.info(
            f"API v2 response: endpoint='create_entity' external_id={entity.external_id}, title={result.title}, permalink={result.permalink}, status_code=201"
        )
        return result


## Update endpoints


@router.put("/entities/{entity_id}", response_model=EntityResponseV2)
async def update_entity_by_id(
    data: Entity,
    response: Response,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
    file_service: FileServiceV2ExternalDep,
    app_config: AppConfigDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
    fast: bool = Query(
        True, description="If true, write quickly and defer indexing to background tasks."
    ),
) -> EntityResponseV2:
    """Update an entity by external ID.

    If the entity doesn't exist, it will be created (upsert behavior).

    Args:
        entity_id: External ID (UUID string)
        data: Updated entity data
        fast: If True, defer indexing to background tasks

    Returns:
        Updated entity with file content
    """
    with telemetry.operation(
        "api.request.knowledge.update_entity",
        entrypoint="api",
        domain="knowledge",
        action="update_entity",
        fast=fast,
    ):
        logger.info(f"API v2 request: update_entity_by_id entity_id={entity_id}")

        with telemetry.scope(
            "api.knowledge.update_entity.load_entity",
            domain="knowledge",
            action="update_entity",
            phase="load_entity",
        ):
            existing = await entity_repository.get_by_external_id(entity_id)
        created = existing is None

        with telemetry.scope(
            "api.knowledge.update_entity.write_entity",
            domain="knowledge",
            action="update_entity",
            phase="write_entity",
            fast=fast,
        ):
            if fast:
                entity = await entity_service.fast_write_entity(data, external_id=entity_id)
                written_content = None
                search_content = None
                response.status_code = 200 if existing else 201
            else:
                if existing:
                    write_result = await entity_service.update_entity_with_content(existing, data)
                    entity = write_result.entity
                    written_content = write_result.content
                    search_content = write_result.search_content
                    response.status_code = 200
                else:
                    write_result = await entity_service.create_entity_with_content(data)
                    entity = write_result.entity
                    written_content = write_result.content
                    search_content = write_result.search_content
                    if entity.external_id != entity_id:
                        entity = await entity_repository.update(
                            entity.id,
                            {"external_id": entity_id},
                        )
                        # external_id fixup only changes the DB row. The file content is unchanged,
                        # so the markdown captured during the write remains valid downstream.
                        if not entity:
                            raise HTTPException(
                                status_code=404,
                                detail=f"Entity with external_id '{entity_id}' not found",
                            )
                    response.status_code = 201

        if fast:
            with telemetry.scope(
                "api.knowledge.update_entity.enqueue_reindex",
                domain="knowledge",
                action="update_entity",
                phase="enqueue_reindex",
                fast=fast,
            ):
                task_scheduler.schedule(
                    "reindex_entity",
                    entity_id=entity.id,
                    project_id=project_id,
                    resolve_relations=created,
                )
        else:
            with telemetry.scope(
                "api.knowledge.update_entity.search_index",
                domain="knowledge",
                action="update_entity",
                phase="search_index",
            ):
                await search_service.index_entity(entity, content=search_content)
            with telemetry.scope(
                "api.knowledge.update_entity.vector_sync",
                domain="knowledge",
                action="update_entity",
                phase="vector_sync",
            ):
                _schedule_vector_sync_if_enabled(
                    task_scheduler=task_scheduler,
                    app_config=app_config,
                    entity_id=entity.id,
                    project_id=project_id,
                )

        result = EntityResponseV2.model_validate(entity)
        if fast:
            result = result.model_copy(update={"observations": [], "relations": []})

        with telemetry.scope(
            "api.knowledge.update_entity.read_content",
            domain="knowledge",
            action="update_entity",
            phase="read_content",
            source="file" if fast else "memory",
        ):
            if fast:
                content = await file_service.read_file_content(entity.file_path)
            else:
                # Non-fast writes already captured the markdown in memory. Reuse it here
                # instead of re-reading the file; format_on_save is the one config that can
                # still make the persisted file diverge because write_file only returns a checksum.
                content = written_content
        result = result.model_copy(update={"content": content})

        logger.info(
            f"API v2 response: external_id={entity_id}, created={created}, status_code={response.status_code}"
        )
        return result


@router.patch("/entities/{entity_id}", response_model=EntityResponseV2)
async def edit_entity_by_id(
    data: EditEntityRequest,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
    file_service: FileServiceV2ExternalDep,
    app_config: AppConfigDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
    fast: bool = Query(
        True, description="If true, write quickly and defer indexing to background tasks."
    ),
) -> EntityResponseV2:
    """Edit an existing entity by external ID using operations like append, prepend, etc.

    Args:
        entity_id: External ID (UUID string)
        data: Edit operation details
        fast: If True, defer indexing to background tasks

    Returns:
        Updated entity with file content

    Raises:
        HTTPException: 404 if entity not found, 400 if edit fails
    """
    with telemetry.operation(
        "api.request.knowledge.edit_entity",
        entrypoint="api",
        domain="knowledge",
        action="edit_entity",
        fast=fast,
    ):
        logger.info(
            f"API v2 request: edit_entity_by_id entity_id={entity_id}, operation='{data.operation}'"
        )

        with telemetry.scope(
            "api.knowledge.edit_entity.load_entity",
            domain="knowledge",
            action="edit_entity",
            phase="load_entity",
        ):
            entity = await entity_repository.get_by_external_id(entity_id)
        if not entity:  # pragma: no cover
            raise HTTPException(
                status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
            )

        try:
            with telemetry.scope(
                "api.knowledge.edit_entity.write_entity",
                domain="knowledge",
                action="edit_entity",
                phase="write_entity",
                fast=fast,
            ):
                if fast:
                    updated_entity = await entity_service.fast_edit_entity(
                        entity=entity,
                        operation=data.operation,
                        content=data.content,
                        section=data.section,
                        find_text=data.find_text,
                        expected_replacements=data.expected_replacements,
                    )
                    written_content = None
                    search_content = None
                else:
                    identifier = entity.permalink or entity.file_path
                    write_result = await entity_service.edit_entity_with_content(
                        identifier=identifier,
                        operation=data.operation,
                        content=data.content,
                        section=data.section,
                        find_text=data.find_text,
                        expected_replacements=data.expected_replacements,
                    )
                    updated_entity = write_result.entity
                    written_content = write_result.content
                    search_content = write_result.search_content

            if fast:
                with telemetry.scope(
                    "api.knowledge.edit_entity.enqueue_reindex",
                    domain="knowledge",
                    action="edit_entity",
                    phase="enqueue_reindex",
                    fast=fast,
                ):
                    task_scheduler.schedule(
                        "reindex_entity",
                        entity_id=updated_entity.id,
                        project_id=project_id,
                    )
            else:
                with telemetry.scope(
                    "api.knowledge.edit_entity.search_index",
                    domain="knowledge",
                    action="edit_entity",
                    phase="search_index",
                ):
                    await search_service.index_entity(updated_entity, content=search_content)
                with telemetry.scope(
                    "api.knowledge.edit_entity.vector_sync",
                    domain="knowledge",
                    action="edit_entity",
                    phase="vector_sync",
                ):
                    _schedule_vector_sync_if_enabled(
                        task_scheduler=task_scheduler,
                        app_config=app_config,
                        entity_id=updated_entity.id,
                        project_id=project_id,
                    )

            result = EntityResponseV2.model_validate(updated_entity)
            if fast:
                result = result.model_copy(update={"observations": [], "relations": []})

            with telemetry.scope(
                "api.knowledge.edit_entity.read_content",
                domain="knowledge",
                action="edit_entity",
                phase="read_content",
                source="file" if fast else "memory",
            ):
                if fast:
                    content = await file_service.read_file_content(updated_entity.file_path)
                else:
                    # Non-fast writes already captured the markdown in memory. Reuse it here
                    # instead of re-reading the file; format_on_save is the one config that can
                    # still make the persisted file diverge because write_file only returns a checksum.
                    content = written_content
            result = result.model_copy(update={"content": content})

            logger.info(
                f"API v2 response: external_id={entity_id}, operation='{data.operation}', status_code=200"
            )

            return result

        except Exception as e:
            logger.error(f"Error editing entity {entity_id}: {e}")
            raise HTTPException(status_code=400, detail=str(e))


## Delete endpoints


@router.delete("/entities/{entity_id}", response_model=DeleteEntitiesResponse)
async def delete_entity_by_id(
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
    search_service=Depends(lambda: None),  # Optional for now
) -> DeleteEntitiesResponse:
    """Delete an entity by external ID.

    Args:
        entity_id: External ID (UUID string)

    Returns:
        Deletion status

    Note: Returns deleted=False if entity doesn't exist (idempotent)
    """
    logger.info(f"API v2 request: delete_entity_by_id entity_id={entity_id}")

    entity = await entity_repository.get_by_external_id(entity_id)
    if entity is None:
        logger.info(f"API v2 response: external_id={entity_id} not found, deleted=False")
        return DeleteEntitiesResponse(deleted=False)

    # Delete the entity using internal ID
    deleted = await entity_service.delete_entity(entity.id)

    # Remove from search index if search service available
    if search_service:
        background_tasks.add_task(search_service.handle_delete, entity)  # pragma: no cover

    logger.info(f"API v2 response: external_id={entity_id}, deleted={deleted}")

    return DeleteEntitiesResponse(deleted=deleted)


## Move endpoint


@router.put("/entities/{entity_id}/move", response_model=EntityResponseV2)
async def move_entity(
    data: MoveEntityRequestV2,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    project_config: ProjectConfigV2ExternalDep,
    app_config: AppConfigDep,
    search_service: SearchServiceV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
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
    logger.info(
        f"API v2 request: move_entity entity_id={entity_id}, destination='{data.destination_path}'"
    )

    try:
        # First, get the entity by external_id to verify it exists
        entity = await entity_repository.get_by_external_id(entity_id)
        if not entity:  # pragma: no cover
            raise HTTPException(
                status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
            )

        # Move the entity using its current file path as identifier
        moved_entity = await entity_service.move_entity(
            identifier=entity.file_path,  # Use file path for resolution
            destination_path=data.destination_path,
            project_config=project_config,
            app_config=app_config,
        )

        # Reindex at new location
        reindexed_entity = await entity_service.link_resolver.resolve_link(data.destination_path)
        if reindexed_entity:
            await search_service.index_entity(reindexed_entity)
            _schedule_vector_sync_if_enabled(
                task_scheduler=task_scheduler,
                app_config=app_config,
                entity_id=reindexed_entity.id,
                project_id=project_id,
            )

        result = EntityResponseV2.model_validate(moved_entity)

        logger.info(f"API v2 response: moved external_id={entity_id} to '{data.destination_path}'")

        return result

    except HTTPException:  # pragma: no cover
        raise  # pragma: no cover
    except Exception as e:
        logger.error(f"Error moving entity: {e}")
        raise HTTPException(status_code=400, detail=str(e))


## Move directory endpoint


@router.post("/move-directory", response_model=DirectoryMoveResult)
async def move_directory(
    data: MoveDirectoryRequestV2,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    project_config: ProjectConfigV2ExternalDep,
    app_config: AppConfigDep,
    search_service: SearchServiceV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
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
            entity = await entity_service.link_resolver.resolve_link(file_path)
            if entity:
                await search_service.index_entity(entity)
                _schedule_vector_sync_if_enabled(
                    task_scheduler=task_scheduler,
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


@router.post("/delete-directory", response_model=DirectoryDeleteResult)
async def delete_directory(
    data: DeleteDirectoryRequestV2,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
) -> DirectoryDeleteResult:
    """Delete all entities in a directory.

    V2 API uses project external_id in the URL path for stable references.
    Deletes all files within a directory, updating database records and
    removing files from the filesystem.

    Args:
        project_id: Project external ID from URL path
        data: Delete request with directory path

    Returns:
        DirectoryDeleteResult with counts and details of deleted files
    """
    logger.info(f"API v2 request: delete_directory directory='{data.directory}'")

    try:
        # Delete the directory using the service
        result = await entity_service.delete_directory(
            directory=data.directory,
        )

        logger.info(
            f"API v2 response: delete_directory "
            f"total={result.total_files}, success={result.successful_deletes}, failed={result.failed_deletes}"
        )
        return result

    except Exception as e:
        logger.error(f"Error deleting directory: {e}")
        raise HTTPException(status_code=400, detail=str(e))
