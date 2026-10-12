"""What a cached read depends on, and what a committed change makes stale.

Every cache key embeds the current version of the eviction group it belongs to, plus
the project and global epochs. Invalidating a group replaces its version, so entries
written under the old one are never read again, and deletes them so they stop holding
memory. A read that races a write stores under the version it observed, which no later
lookup uses, and that one entry expires on its own.

Groups:

- one per note: that note's rendered response
- one per project for content: search, listings with titles and modified times,
  recent activity
- the project epoch: everything in the project, including name lookups and the
  folder structure, which change only when notes are created, renamed, moved or
  deleted
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ReadCacheOperation(StrEnum):
    """Read operations served from the cache."""

    directory_list = "directory_list"
    directory_structure = "directory_structure"
    directory_tree = "directory_tree"
    entity = "entity"
    recent_activity = "recent_activity"
    resolve = "resolve"
    search = "search"


class ReadCacheInvalidationStatus(StrEnum):
    """Outcome of one invalidation attempt."""

    invalidated = "invalidated"
    unavailable = "unavailable"


def canonical_uuid(value: str, *, label: str) -> str:
    """Return one canonical UUID spelling, failing on anything else."""
    if not value:
        raise ValueError(f"read-cache {label} must not be empty")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise ValueError(f"read-cache {label} must be a valid UUID") from error


# --- Eviction groups a cached read belongs to ---


@dataclass(frozen=True, slots=True)
class NoteGroup:
    """One note's rendered response: body, observations, relations, status."""

    entity_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", canonical_uuid(self.entity_id, label="note id"))

    @property
    def name(self) -> str:
        return f"note:{self.entity_id}"


@dataclass(frozen=True, slots=True)
class ContentGroup:
    """Reads that change with any note's content."""

    @property
    def name(self) -> str:
        return "content"


@dataclass(frozen=True, slots=True)
class ProjectGroup:
    """Reads that change only when the project's set of notes or names changes."""

    @property
    def name(self) -> str:
        return "project"


type ReadCacheGroup = NoteGroup | ContentGroup | ProjectGroup

CONTENT = ContentGroup()
PROJECT = ProjectGroup()


def group_kind(group: ReadCacheGroup) -> str:
    """Bounded label for telemetry: never a note id."""
    match group:
        case NoteGroup():
            return "note"
        case ContentGroup():
            return "content"
        case ProjectGroup():
            return "project"


# --- Changes ---


@dataclass(frozen=True, slots=True)
class ReadCacheChange:
    """The groups one committed change made stale. An empty change retires nothing."""

    project: bool = False
    content: bool = False
    notes: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "notes",
            frozenset(canonical_uuid(note_id, label="note id") for note_id in self.notes),
        )

    @classmethod
    def whole_project(cls) -> "ReadCacheChange":
        """Retire every cached read in the project.

        Used for creates, renames, moves and deletes, which change name lookups and
        the links rendered in other notes, and whenever a writer cannot say exactly
        what it changed.
        """
        return cls(project=True)

    @classmethod
    def note_content(cls, entity_ids: Iterable[str]) -> "ReadCacheChange":
        """A body change to these notes: their responses and the content-wide reads."""
        return cls(content=True, notes=frozenset(entity_ids))

    @classmethod
    def search_index(cls) -> "ReadCacheChange":
        """Derived search state (vector chunks) changed: only content-wide reads.

        Note responses do not include embeddings, so no note's own entry is stale.
        """
        return cls(content=True)

    def __or__(self, other: "ReadCacheChange") -> "ReadCacheChange":
        return ReadCacheChange(
            project=self.project or other.project,
            content=self.content or other.content,
            notes=self.notes | other.notes,
        )

    @property
    def is_empty(self) -> bool:
        return not (self.project or self.content or self.notes)

    @property
    def groups(self) -> tuple[ReadCacheGroup, ...]:
        """The group versions this change replaces, in a stable order."""
        # The project epoch is part of every key, so replacing it alone retires
        # every group; per-group bumps under it would only add Redis writes.
        if self.project:
            return (PROJECT,)
        bumped: list[ReadCacheGroup] = []
        if self.content:
            bumped.append(CONTENT)
        bumped.extend(NoteGroup(note_id) for note_id in sorted(self.notes))
        return tuple(bumped)


class ReadCacheUnavailable(RuntimeError):
    """The cache backend could not complete an optional operation."""


class ReadCacheInvalidator(Protocol):
    """Capability for retiring cached reads after a committed change."""

    async def invalidate(
        self,
        project_id: str,
        change: ReadCacheChange,
    ) -> ReadCacheInvalidationStatus:
        """Retire every cached read in a group ``change`` names."""

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        """Retire every cached read in one project."""
