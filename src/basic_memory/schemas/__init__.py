"""Knowledge graph schema exports.

This module exports all schema classes to simplify imports.
Rather than importing from individual schema files, you can
import everything from basic_memory.schemas.
"""

# Base types and models
from basic_memory.schemas.base import (
    Observation,
    NoteType,
    RelationType,
    Relation,
    Entity,
)

# Delete operation models
from basic_memory.schemas.delete import (
    DeleteEntitiesRequest,
)

# Request models
from basic_memory.schemas.request import (
    SearchNodesRequest,
    GetEntitiesRequest,
    CreateRelationsRequest,
)

# Response models
from basic_memory.schemas.response import (
    SQLAlchemyModel,
    ObservationResponse,
    RelationResponse,
    EntityResponse,
    EntityListResponse,
    SearchNodesResponse,
    DeleteEntitiesResponse,
)

from basic_memory.schemas.project_info import (
    ProjectStatistics,
    ActivityMetrics,
    SystemStatus,
    EmbeddingStatus,
    ProjectInfoResponse,
)

from basic_memory.schemas.project_index import (
    ProjectIndexObservedFileResponse,
    ProjectIndexRunResponse,
    ProjectIndexStatusResponse,
)

from basic_memory.schemas.directory import (
    DirectoryNode,
)

from basic_memory.schemas.document import (
    DocumentAgentObservationV1,
    DocumentAgentOutputV1,
    DocumentAgentRelationV1,
    DocumentExtractionStatus,
    DocumentExtractionV1,
    DocumentIngestionFailureV1,
    DocumentIngestionRunFrontmatterV1,
    DocumentIngestionRunMarkdownV1,
    DocumentIngestionRunOutputV1,
    DocumentIngestionRunStateV1,
    DocumentIngestionStage,
    DocumentIngestionV1,
    DocumentMarkdownV1,
    DocumentMetadataV1,
    DocumentNoteFrontmatterV1,
    DocumentRevisionKind,
    DocumentRevisionReferenceV1,
    DocumentSourceV1,
    assemble_document_ingestion_run_markdown,
    assemble_document_markdown,
    derive_document_ingestion_run_id,
    derive_document_ingestion_run_path,
    derive_document_note_external_id,
    derive_document_note_path,
    document_markdown_checksum,
    enrich_document_markdown,
    parse_document_ingestion_run_markdown,
    parse_document_markdown,
    require_document_ingestion_transition,
)

# For convenient imports, export all models
__all__ = [
    # Base
    "Observation",
    "NoteType",
    "RelationType",
    "Relation",
    "Entity",
    # Requests
    "SearchNodesRequest",
    "GetEntitiesRequest",
    "CreateRelationsRequest",
    # Responses
    "SQLAlchemyModel",
    "ObservationResponse",
    "RelationResponse",
    "EntityResponse",
    "EntityListResponse",
    "SearchNodesResponse",
    "DeleteEntitiesResponse",
    # Delete Operations
    "DeleteEntitiesRequest",
    # Project Info
    "ProjectStatistics",
    "ActivityMetrics",
    "SystemStatus",
    "EmbeddingStatus",
    "ProjectInfoResponse",
    "ProjectIndexObservedFileResponse",
    "ProjectIndexRunResponse",
    "ProjectIndexStatusResponse",
    # Directory
    "DirectoryNode",
    # Document ingestion
    "DocumentAgentObservationV1",
    "DocumentAgentOutputV1",
    "DocumentAgentRelationV1",
    "DocumentExtractionStatus",
    "DocumentExtractionV1",
    "DocumentIngestionFailureV1",
    "DocumentIngestionRunFrontmatterV1",
    "DocumentIngestionRunMarkdownV1",
    "DocumentIngestionRunOutputV1",
    "DocumentIngestionRunStateV1",
    "DocumentIngestionStage",
    "DocumentIngestionV1",
    "DocumentMarkdownV1",
    "DocumentMetadataV1",
    "DocumentNoteFrontmatterV1",
    "DocumentRevisionKind",
    "DocumentRevisionReferenceV1",
    "DocumentSourceV1",
    "assemble_document_ingestion_run_markdown",
    "assemble_document_markdown",
    "derive_document_ingestion_run_id",
    "derive_document_ingestion_run_path",
    "derive_document_note_external_id",
    "derive_document_note_path",
    "document_markdown_checksum",
    "enrich_document_markdown",
    "parse_document_ingestion_run_markdown",
    "parse_document_markdown",
    "require_document_ingestion_transition",
]
