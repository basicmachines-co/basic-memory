"""Temporary shared gateway service namespace.

This package collects route/CLI-facing domain services that are being moved out
of Basic Memory Cloud. The name is intentionally transitional: the services
should move to a clearer application/operations namespace once the local and
cloud adapter boundaries settle.
"""

from basic_memory.gateway.directory_deletes import (
    DirectoryDeleteService,
    DirectoryDeleteServiceError,
    DirectoryDeleteSessionMaker,
    directory_delete_service_error_from_rejection,
)
from basic_memory.gateway.note_content_reads import (
    NoteContentQueryService,
)
from basic_memory.gateway.note_content_writes import (
    NoteContentMutationService,
    NoteContentMutationServiceError,
    note_content_mutation_error_from_rejection,
)

__all__ = [
    "DirectoryDeleteService",
    "DirectoryDeleteServiceError",
    "DirectoryDeleteSessionMaker",
    "NoteContentMutationService",
    "NoteContentMutationServiceError",
    "NoteContentQueryService",
    "directory_delete_service_error_from_rejection",
    "note_content_mutation_error_from_rejection",
]
