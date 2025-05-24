"""Schemas for import services."""

from typing import Dict, Optional

from pydantic import BaseModel


class ImportResult(BaseModel):
    """Common import result schema."""

    import_count: Dict[str, int]
    success: bool
    error_message: Optional[str] = None


class ChatImportResult(ImportResult):
    """Result schema for chat imports."""

    conversations: int = 0
    messages: int = 0


class ProjectImportResult(ImportResult):
    """Result schema for project imports."""

    documents: int = 0
    prompts: int = 0


class EntityImportResult(ImportResult):
    """Result schema for entity imports."""

    entities: int = 0
    relations: int = 0
