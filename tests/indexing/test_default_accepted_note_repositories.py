"""Tests for the default accepted-note repository provider."""

from basic_memory.indexing.accepted_note_mutation_runner import (
    DefaultAcceptedNoteRepositories,
    build_default_accepted_note_repositories,
)
from basic_memory.repository import NoteContentRepository
from basic_memory.repository.accepted_note_search_repository import AcceptedNoteSearchRepository
from basic_memory.repository.entity_repository import EntityRepository


def test_build_default_accepted_note_repositories_wires_core_repositories() -> None:
    """The default provider should satisfy lookup and write repository needs."""
    repositories = build_default_accepted_note_repositories()

    assert isinstance(repositories, DefaultAcceptedNoteRepositories)
    assert isinstance(repositories.entity_repository(7), EntityRepository)
    assert repositories.entity_repository(7).project_id == 7
    assert isinstance(repositories.pending_entity_repository(8), EntityRepository)
    assert repositories.pending_entity_repository(8).project_id == 8
    assert isinstance(repositories.note_content_repository(9), NoteContentRepository)
    assert repositories.note_content_repository(9).project_id == 9
    assert isinstance(repositories.search_repository(10), AcceptedNoteSearchRepository)
    assert repositories.search_repository(10).project_id == 10
