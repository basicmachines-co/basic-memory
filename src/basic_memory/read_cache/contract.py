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
- one per project for names: name and link lookups and the folder structure, which
  change only when notes are created, renamed, moved or deleted
- the project epoch: part of every key in the project, so replacing it retires
  everything; used when a writer cannot say what it changed
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
    link_resolve = "link_resolve"
    project_resolve = "project_resolve"
    recent_activity = "recent_activity"
    resolve = "resolve"
    resource = "resource"
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


# Workspace-wide reads (project resolve) have no project in their path. They are
# kept under this reserved project id, whose project epoch is retired whenever the
# set of projects changes. No real project has the nil UUID.
WORKSPACE_ENTRIES_PROJECT_ID = "00000000-0000-0000-0000-000000000000"


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
class NamesGroup:
    """Reads that change only when the project's set of notes or their names change.

    Name and link lookups and the folder structure. A body edit leaves them alone.
    """

    @property
    def name(self) -> str:
        return "names"


@dataclass(frozen=True, slots=True)
class ProjectGroup:
    """The project epoch, embedded in every key of the project.

    Reads keyed only by it change when anything in the project does. Replacing it
    retires every cached read in the project.
    """

    @property
    def name(self) -> str:
        return "project"


type ReadCacheGroup = NoteGroup | ContentGroup | NamesGroup | ProjectGroup

CONTENT = ContentGroup()
NAMES = NamesGroup()
PROJECT = ProjectGroup()


def group_kind(group: ReadCacheGroup) -> str:
    """Bounded label for telemetry: never a note id."""
    match group:
        case NoteGroup():
            return "note"
        case ContentGroup():
            return "content"
        case NamesGroup():
            return "names"
        case ProjectGroup():
            return "project"


# --- Changes ---


@dataclass(frozen=True, slots=True)
class ReadCacheChange:
    """The groups one committed change made stale. An empty change retires nothing."""

    project: bool = False
    names: bool = False
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

        Used whenever a writer cannot say exactly what it changed: failure paths,
        imports, bulk moves and reindexes.
        """
        return cls(project=True)

    @classmethod
    def note_identity(cls, entity_ids: Iterable[str]) -> "ReadCacheChange":
        """A note was created, renamed, moved or deleted.

        Name lookups and the folder structure change, and so do the content-wide
        reads. ``entity_ids`` are the notes whose own responses changed: the note
        itself and any note that renders a link to or from it. No other note's
        response can show the change, so their entries stay.
        """
        return cls(names=True, content=True, notes=frozenset(entity_ids))

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
            names=self.names or other.names,
            content=self.content or other.content,
            notes=self.notes | other.notes,
        )

    @property
    def is_empty(self) -> bool:
        return not (self.project or self.names or self.content or self.notes)

    @property
    def groups(self) -> tuple[ReadCacheGroup, ...]:
        """The group versions this change replaces, in a stable order."""
        # The project epoch is part of every key, so replacing it alone retires
        # every group; per-group bumps under it would only add Redis writes.
        if self.project:
            return (PROJECT,)
        bumped: list[ReadCacheGroup] = []
        if self.names:
            bumped.append(NAMES)
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
