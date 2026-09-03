"""Read-only accepted note-content hydration endpoints."""

from fastapi import APIRouter

import logfire
from basic_memory.deps import NoteContentQueryServiceDep, ProjectExternalIdPathDep, SessionDep
from basic_memory.schemas.v2.accepted_content import (
    AcceptedNoteContentBatchRequest,
    AcceptedNoteContentBatchResponse,
    AcceptedNoteContentItem,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge-v2"])


@router.api_route(
    "/entities/batch",
    methods=["QUERY"],
    response_model=AcceptedNoteContentBatchResponse,
)
async def get_accepted_note_content_batch(
    project_id: ProjectExternalIdPathDep,
    data: AcceptedNoteContentBatchRequest,
    note_content_query_service: NoteContentQueryServiceDep,
    session: SessionDep,
) -> AcceptedNoteContentBatchResponse:
    """Return current accepted Markdown for a bounded set of project notes."""
    with logfire.span(
        "api.request.knowledge.get_accepted_note_content_batch",
        entrypoint="api",
        domain="knowledge",
        action="get_accepted_note_content_batch",
        batch_size=len(data.entity_ids),
    ) as span:
        accepted_contents = await note_content_query_service.get_accepted_note_content_batch(
            project_id=project_id,
            entity_external_ids=data.entity_ids,
            session=session,
        )
        content_by_external_id = {item.external_id: item.content for item in accepted_contents}
        items = [
            AcceptedNoteContentItem(
                external_id=external_id,
                content=content_by_external_id[external_id],
            )
            for external_id in data.entity_ids
            if external_id in content_by_external_id
        ]
        missing_entity_ids = [
            external_id
            for external_id in data.entity_ids
            if external_id not in content_by_external_id
        ]
        span.set_attribute("missing_count", len(missing_entity_ids))
        return AcceptedNoteContentBatchResponse(
            items=items,
            missing_entity_ids=missing_entity_ids,
        )
