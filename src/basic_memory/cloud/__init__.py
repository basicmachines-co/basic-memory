"""Shared cloud-runtime service namespace.

This package collects route/CLI-facing domain services shared by local Basic
Memory and Basic Memory Cloud. Core owns the orchestration; runtimes provide
storage, queue, session, and tenant adapters around these services.
"""

from basic_memory.cloud.directory_deletes import (
    DirectoryDeleteService,
    DirectoryDeleteServiceError,
    DirectoryDeleteSessionMaker,
    directory_delete_service_error_from_rejection,
)
from basic_memory.cloud.note_content_reads import (
    NoteContentQueryService,
)
from basic_memory.cloud.note_content_materialization import (
    LocalNoteContentMaterializationProvider,
)
from basic_memory.cloud.note_content_writes import (
    NoteContentMutationService,
    NoteContentMutationServiceError,
    note_content_mutation_error_from_rejection,
)
from basic_memory.cloud.project_deletes import (
    ProjectDeleteAcceptanceError,
    ProjectDeleteAcceptanceRequest,
    ProjectDeleteAcceptanceService,
    ProjectDeleteJobEnqueuer,
)

__all__ = [
    "DirectoryDeleteService",
    "DirectoryDeleteServiceError",
    "DirectoryDeleteSessionMaker",
    "NoteContentMutationService",
    "NoteContentMutationServiceError",
    "NoteContentQueryService",
    "LocalNoteContentMaterializationProvider",
    "ProjectDeleteAcceptanceError",
    "ProjectDeleteAcceptanceRequest",
    "ProjectDeleteAcceptanceService",
    "ProjectDeleteJobEnqueuer",
    "directory_delete_service_error_from_rejection",
    "note_content_mutation_error_from_rejection",
]
