"""Tests for the local accepted-note repository bundle."""

from basic_memory.deps.services import LocalAcceptedNoteRepositories
from basic_memory.indexing.accepted_note_mutation_runner import AcceptedNoteMutationRepositories
from basic_memory.indexing.accepted_note_write_runner import AcceptedNoteWriteRepositories
from basic_memory.repository import NoteContentRepository
from basic_memory.repository.accepted_note_search_repository import AcceptedNoteSearchRepository
from basic_memory.repository.entity_repository import EntityRepository


def test_local_accepted_note_repositories_wires_project_scoped_repositories() -> None:
    """One concrete bundle satisfies lookup and write repository needs per project."""
    repositories = LocalAcceptedNoteRepositories()

    # The same bundle instance serves both capability seams of the mutation runner.
    lookup_repositories: AcceptedNoteMutationRepositories = repositories
    write_repositories: AcceptedNoteWriteRepositories = repositories
    assert lookup_repositories is write_repositories

    assert isinstance(repositories.entity_repository(7), EntityRepository)
    assert repositories.entity_repository(7).project_id == 7
    assert isinstance(repositories.pending_entity_repository(8), EntityRepository)
    assert repositories.pending_entity_repository(8).project_id == 8
    assert isinstance(repositories.note_content_repository(9), NoteContentRepository)
    assert repositories.note_content_repository(9).project_id == 9
    assert isinstance(repositories.search_repository(10), AcceptedNoteSearchRepository)
    assert repositories.search_repository(10).project_id == 10
