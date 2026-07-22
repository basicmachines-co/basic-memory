"""Project-scoped repositories for accepted-note mutations."""

from collections.abc import Callable
from dataclasses import dataclass

from basic_memory.repository import NoteContentRepository, ObservationRepository, RelationRepository
from basic_memory.repository.accepted_note_search_repository import AcceptedNoteSearchRepository
from basic_memory.repository.accepted_note_vector_cleanup import (
    ProjectIndexExternalVectorCleaner,
)
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.runtime.storage import ProjectId


@dataclass(frozen=True, slots=True)
class AcceptedNoteRepositories:
    """Core repository bundle using the caller-owned transaction."""

    external_vector_cleaner_factory: (
        Callable[[ProjectId], ProjectIndexExternalVectorCleaner] | None
    ) = None

    def entity_repository(self, project_id: ProjectId) -> EntityRepository:
        return EntityRepository(project_id=project_id)

    def pending_entity_repository(self, project_id: ProjectId) -> EntityRepository:
        return EntityRepository(project_id=project_id)

    def note_content_repository(self, project_id: ProjectId) -> NoteContentRepository:
        return NoteContentRepository(project_id=project_id)

    def search_repository(self, project_id: ProjectId) -> AcceptedNoteSearchRepository:
        external_vector_cleaner = (
            self.external_vector_cleaner_factory(project_id)
            if self.external_vector_cleaner_factory is not None
            else None
        )
        return AcceptedNoteSearchRepository(
            project_id=project_id,
            external_vector_cleaner=external_vector_cleaner,
        )

    def observation_repository(self, project_id: ProjectId) -> ObservationRepository:
        return ObservationRepository(project_id=project_id)

    def relation_repository(self, project_id: ProjectId) -> RelationRepository:
        return RelationRepository(project_id=project_id)
