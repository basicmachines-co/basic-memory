"""Bounded API models for accepted note-content hydration."""

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

AcceptedNoteExternalId = Annotated[str, Field(min_length=1, max_length=500)]


class AcceptedNoteContentBatchRequest(BaseModel):
    """One bounded, project-scoped batch of accepted note identifiers."""

    entity_ids: list[AcceptedNoteExternalId] = Field(min_length=1, max_length=25)

    @field_validator("entity_ids")
    @classmethod
    def require_unique_entity_ids(cls, entity_ids: list[str]) -> list[str]:
        """Reject duplicate work instead of returning ambiguous result positions."""
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entity_ids must be unique")
        return entity_ids


class AcceptedNoteContentItem(BaseModel):
    """Accepted Markdown for one requested note."""

    external_id: str
    content: str


class AcceptedNoteContentBatchResponse(BaseModel):
    """Ordered batch results plus identifiers without accepted DB content."""

    items: list[AcceptedNoteContentItem]
    missing_entity_ids: list[str]
