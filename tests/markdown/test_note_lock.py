"""The unlock-only rule a verified person's write must satisfy on a locked note."""

import pytest

from basic_memory.markdown.note_lock import is_unlock_only, note_is_locked

LOCKED = "---\ntitle: Runbook\ntype: note\nlocked: true\n---\n\nKeep this exact content.\n"


@pytest.mark.parametrize(
    "proposed",
    [
        "---\ntitle: Runbook\ntype: note\n---\n\nKeep this exact content.\n",
        "---\ntitle: Runbook\ntype: note\nlocked: false\n---\n\nKeep this exact content.\n",
        # Key order and the server-stamped updater are not the caller's change.
        "---\ntype: note\ntitle: Runbook\nupdated_by: Alice\n---\n\nKeep this exact content.\n",
    ],
)
def test_removing_only_the_lock_is_an_unlock(proposed: str) -> None:
    assert is_unlock_only(locked_markdown=LOCKED, proposed_markdown=proposed)
    assert not note_is_locked(proposed)


@pytest.mark.parametrize(
    "proposed",
    [
        # Still locked, so this is an edit of a locked note, not an unlock.
        LOCKED,
        "---\ntitle: Runbook\ntype: note\nlocked: true\n---\n\nEdited.\n",
        # Unlocking and editing in one write.
        "---\ntitle: Runbook\ntype: note\n---\n\nEdited.\n",
        "---\ntitle: Renamed\ntype: note\n---\n\nKeep this exact content.\n",
        "---\ntitle: Runbook\ntype: note\nstatus: draft\n---\n\nKeep this exact content.\n",
        # Weakening the lock to a value that is not false.
        '---\ntitle: Runbook\ntype: note\nlocked: "true"\n---\n\nKeep this exact content.\n',
        # No usable frontmatter at all.
        "Keep this exact content.\n",
        "---\ntitle: [unclosed\n---\n\nKeep this exact content.\n",
    ],
)
def test_any_other_change_is_not_an_unlock(proposed: str) -> None:
    assert not is_unlock_only(locked_markdown=LOCKED, proposed_markdown=proposed)
