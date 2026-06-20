"""Public indexing package export contracts."""

from basic_memory.indexing import (
    AcceptedNoteCreateMutation,
    AcceptedNoteMutationDependencies,
    run_accepted_note_create,
)


def test_accepted_note_mutation_runner_is_exported_from_indexing_package() -> None:
    """Cloud and future runtimes should import stable indexing contracts."""
    assert AcceptedNoteCreateMutation.__name__ == "AcceptedNoteCreateMutation"
    assert AcceptedNoteMutationDependencies.__name__ == "AcceptedNoteMutationDependencies"
    assert run_accepted_note_create.__name__ == "run_accepted_note_create"
