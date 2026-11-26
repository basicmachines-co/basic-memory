"""API write protection carried by canonical Markdown, not indexed metadata."""

from dataclasses import dataclass

from basic_memory.file_utils import (
    ParseError,
    has_frontmatter,
    parse_frontmatter,
    remove_frontmatter,
)
from basic_memory.services.note_authorship import UPDATED_BY_FIELD


LOCKED_NOTE_MESSAGE = (
    "This note is locked (locked: true). Basic Memory cannot edit, overwrite, delete, "
    "or unlock it. Ask a person to unlock it in the Basic Memory web app, or remove "
    "locked: true by editing the Markdown file directly."
)

LOCKED_FIELD = "locked"
PERMALINK_FIELD = "permalink"


# --- Who may remove a lock ---
#
# The lock exists so an agent cannot change or unlock a protected note by accident.
# Core cannot tell a person from an agent: every API caller looks the same. Only the
# host runtime that authenticated the caller can vouch for a person, so it passes
# that decision in explicitly. Everything else, including every local caller, is an
# agent and keeps the one-way lock.


@dataclass(frozen=True, slots=True)
class PersonInWebApp:
    """A person the host verified is saving from its own editor, not through an agent."""


@dataclass(frozen=True, slots=True)
class AgentWriter:
    """Any caller the host has not vouched for as a person. The lock is one-way for it."""


type NoteLockAuthority = PersonInWebApp | AgentWriter

AGENT_WRITER = AgentWriter()


def note_is_locked(markdown_content: str) -> bool:
    """Only the YAML boolean true opts a note into API write protection."""
    if not has_frontmatter(markdown_content):
        return False
    try:
        metadata = parse_frontmatter(markdown_content)
    except ParseError:
        # Offline malformed notes remain supported resources, not valid lock declarations.
        # API edits cannot reach this escape hatch: the accepted locked predecessor is
        # checked before any proposed replacement can introduce malformed frontmatter.
        return False
    return metadata.get(LOCKED_FIELD) is True


def is_unlock_only(
    *,
    locked_markdown: str,
    proposed_markdown: str,
    entity_permalink: str | None,
    stamped_updated_by: str | None,
) -> bool:
    """Return whether the proposed Markdown only removes the lock from the locked note.

    Unlocking is one deliberate action: the proposed note must drop `locked` (or set it
    to false) and change nothing else, so a person cannot unlock and edit in one write.
    `proposed_markdown` is what the server will write, so it also carries what the
    server adds to every update: the note's permalink when the file had none, and the
    `updated_by` stamp when the write names an author. Those are expected, not edits.
    """
    if not has_frontmatter(proposed_markdown):
        return False
    try:
        locked_metadata = parse_frontmatter(locked_markdown)
        proposed_metadata = parse_frontmatter(proposed_markdown)
    except ParseError:
        return False
    if proposed_metadata.get(LOCKED_FIELD, False) is not False:
        return False

    # What an unlock of this note writes: the same frontmatter without the lock, plus
    # what the server writes on any update.
    expected_metadata = {
        key: value for key, value in locked_metadata.items() if key != LOCKED_FIELD
    }
    if entity_permalink is not None:
        expected_metadata.setdefault(PERMALINK_FIELD, entity_permalink)
    if stamped_updated_by is not None:
        expected_metadata[UPDATED_BY_FIELD] = stamped_updated_by
    proposed_without_lock = {
        key: value for key, value in proposed_metadata.items() if key != LOCKED_FIELD
    }
    return expected_metadata == proposed_without_lock and remove_frontmatter(
        locked_markdown
    ) == remove_frontmatter(proposed_markdown)
