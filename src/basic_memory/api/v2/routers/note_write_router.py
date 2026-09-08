"""One path-addressed write, with expected outcomes preserved across HTTP."""

from dataclasses import asdict
from typing import Annotated, assert_never

import logfire
from fastapi import APIRouter, HTTPException, Path

from basic_memory.deps import (
    AppConfigDep,
    EntityRepositoryV2ExternalDep,
    EntityVectorSyncSchedulerDep,
    NoteContentMaterializationProviderDep,
    NoteContentMutationServiceDep,
    ProjectExternalIdPathDep,
    ProjectRepositoryDep,
    RelationResolutionSchedulerDep,
    SessionMakerDep,
)
from basic_memory.runtime.note_content import runtime_note_content_payload_as_dict
from basic_memory.schemas.v2.entity import EntityResponseV2
from basic_memory.schemas.v2.note_write import (
    NoteAlreadyExists,
    NoteCreated,
    NoteLocked,
    NoteTargetMoved,
    NoteUpdated,
    WriteNoteRequest,
    WriteNoteResponse,
)
from basic_memory.services.note_content_writes import (
    NoteContentMutationServiceError,
    note_content_mutation_error_from_rejection,
)
from basic_memory.services.note_write_outcomes import (
    AlreadyExists,
    Created,
    Locked,
    Rejected,
    TargetMoved,
    Updated,
)
from basic_memory.utils import build_permalink_resolution_candidates
from basic_memory.workspace_context import current_workspace_permalink_context

router = APIRouter()


@router.post("/write", response_model=WriteNoteResponse)
async def write_note(
    data: WriteNoteRequest,
    project_id: ProjectExternalIdPathDep,
    project_external_id: Annotated[str, Path(alias="project_id")],
    project_repository: ProjectRepositoryDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    session_maker: SessionMakerDep,
    note_content_mutation_service: NoteContentMutationServiceDep,
    note_content_materialization_provider: NoteContentMaterializationProviderDep,
    vector_sync_scheduler: EntityVectorSyncSchedulerDep,
    relation_resolution_scheduler: RelationResolutionSchedulerDep,
    app_config: AppConfigDep,
) -> WriteNoteResponse:
    """Create at the requested path or replace the note that currently owns it."""
    with logfire.span(
        "api.request.knowledge.write_note",
        entrypoint="api",
        domain="knowledge",
        action="write_note",
    ):
        # Release the read connection before the mutation opens its own transaction.
        async with session_maker() as session:
            project = await project_repository.get_by_id(session, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        workspace = current_workspace_permalink_context()
        candidates = build_permalink_resolution_candidates(
            data.note.file_path,
            project.permalink,
            include_project=app_config.permalinks_include_project,
            workspace_permalink=(
                workspace.workspace_slug
                if workspace and workspace.should_prefix_permalinks
                else None
            ),
        )
        try:
            outcome = await note_content_mutation_service.write_note(
                project_external_id=project_external_id,
                data=data.note,
                overwrite=data.overwrite,
                entity_repository=entity_repository,
                permalink_candidates=candidates,
                user_profile_id=None,
                source="api",
            )
        except NoteContentMutationServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error
        match outcome:
            case Created(change=change) | Updated(change=change):
                accepted = await note_content_materialization_provider.materialize_write_change(
                    change
                )
                entity = EntityResponseV2.model_validate(
                    runtime_note_content_payload_as_dict(accepted.payload)
                )
                # Runtime-injected schedulers preserve local and Cloud publication behavior.
                if app_config.semantic_search_enabled:
                    vector_sync_scheduler.schedule_entity_vector_sync(
                        entity_id=entity.id, project_id=project_id
                    )
                relation_resolution_scheduler.schedule_relation_resolution(project_id=project_id)
                match outcome:
                    case Created():
                        return NoteCreated(entity=entity)
                    case Updated():
                        return NoteUpdated(entity=entity)
                    case _:
                        assert_never(outcome)
            case AlreadyExists(file_path=file_path):
                return NoteAlreadyExists(file_path=file_path)
            case TargetMoved(note=note):
                return NoteTargetMoved(**asdict(note))
            case Locked(message=message):
                return NoteLocked(message=message)
            case Rejected(rejection=rejection):
                error = note_content_mutation_error_from_rejection(rejection)
                raise HTTPException(status_code=error.status_code, detail=error.detail)
            case _:
                assert_never(outcome)
