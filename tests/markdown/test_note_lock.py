"""The unlock-only rule a verified person's write must satisfy on a locked note."""

import pytest

from basic_memory.markdown.note_lock import is_unlock_only, note_is_locked

# The locked note's own permalink, which every update writes into the frontmatter.
PERMALINK = "runbook"
LOCKED_WITH_PERMALINK = (
    "---\ntitle: Runbook\ntype: note\npermalink: runbook\nlocked: true\n---\n\n"
    "Keep this exact content.\n"
)


def _unlock_only(
    proposed: str,
    *,
    locked: str = LOCKED_WITH_PERMALINK,
    stamped_updated_by: str | None = None,
) -> bool:
    return is_unlock_only(
        locked_markdown=locked,
        proposed_markdown=proposed,
        entity_permalink=PERMALINK,
        stamped_updated_by=stamped_updated_by,
    )


@pytest.mark.parametrize(
    "proposed",
    [
        "---\ntitle: Runbook\ntype: note\npermalink: runbook\n---\n\nKeep this exact content.\n",
        "---\ntitle: Runbook\ntype: note\npermalink: runbook\nlocked: false\n---\n\n"
        "Keep this exact content.\n",
        # Key order is not a change.
        "---\npermalink: runbook\ntype: note\ntitle: Runbook\n---\n\nKeep this exact content.\n",
    ],
)
def test_removing_only_the_lock_is_an_unlock(proposed: str) -> None:
    assert _unlock_only(proposed)
    assert not note_is_locked(proposed)


@pytest.mark.parametrize(
    "proposed",
    [
        # Still locked, so this is an edit of a locked note, not an unlock.
        LOCKED_WITH_PERMALINK,
        "---\ntitle: Runbook\ntype: note\npermalink: runbook\nlocked: true\n---\n\nEdited.\n",
        # Unlocking and editing in one write.
        "---\ntitle: Runbook\ntype: note\npermalink: runbook\n---\n\nEdited.\n",
        "---\ntitle: Renamed\ntype: note\npermalink: runbook\n---\n\nKeep this exact content.\n",
        "---\ntitle: Runbook\ntype: note\npermalink: runbook\nstatus: draft\n---\n\n"
        "Keep this exact content.\n",
        "---\ntitle: Runbook\ntype: note\npermalink: elsewhere\n---\n\nKeep this exact content.\n",
        # Weakening the lock to a value that is not false.
        '---\ntitle: Runbook\ntype: note\npermalink: runbook\nlocked: "true"\n---\n\n'
        "Keep this exact content.\n",
        # No usable frontmatter at all.
        "Keep this exact content.\n",
        "---\ntitle: [unclosed\n---\n\nKeep this exact content.\n",
    ],
)
def test_any_other_change_is_not_an_unlock(proposed: str) -> None:
    assert not _unlock_only(proposed)
    assert not _unlock_only(proposed, stamped_updated_by="Alice")


UNLOCKED_BY_ALICE = (
    "---\ntitle: Runbook\ntype: note\npermalink: runbook\nupdated_by: Alice\n---\n\n"
    "Keep this exact content.\n"
)


def test_the_servers_updated_by_stamp_is_not_the_callers_change() -> None:
    assert _unlock_only(UNLOCKED_BY_ALICE, stamped_updated_by="Alice")


def test_an_updated_by_the_server_did_not_stamp_is_the_callers_change() -> None:
    # With no author, nothing overwrites updated_by, so the caller wrote it.
    assert not _unlock_only(UNLOCKED_BY_ALICE, stamped_updated_by=None)
    # With an author, only the server's own stamp is expected.
    assert not _unlock_only(UNLOCKED_BY_ALICE, stamped_updated_by="Bob")


def test_the_permalink_the_server_adds_to_a_note_without_one_is_not_the_callers_change() -> None:
    locked_without_permalink = (
        "---\ntitle: Runbook\ntype: note\nlocked: true\n---\n\nKeep this exact content.\n"
    )
    with_server_permalink = (
        "---\ntitle: Runbook\ntype: note\npermalink: runbook\n---\n\nKeep this exact content.\n"
    )
    with_other_permalink = with_server_permalink.replace("permalink: runbook", "permalink: other")

    assert _unlock_only(with_server_permalink, locked=locked_without_permalink)
    assert not _unlock_only(with_other_permalink, locked=locked_without_permalink)
