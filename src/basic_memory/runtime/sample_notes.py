"""Portable runtime values for sample-note initialization."""

from dataclasses import dataclass, replace
from typing import Self


@dataclass(frozen=True, slots=True)
class RuntimeSampleNotesInitializationResult:
    """Typed result for initializing sample notes before workflow serialization."""

    project_created: int = 0
    notes_created: int = 0
    notes_failed: int = 0
    index_jobs_enqueued: int = 0
    attempted: bool = True

    @classmethod
    def started(cls) -> Self:
        """Create a result for an initialization attempt that reached file work."""
        return cls(attempted=True)

    @classmethod
    def not_started(cls) -> Self:
        """Create the legacy empty workflow result for missing prerequisites."""
        return cls(attempted=False)

    def with_project_created(self, created: bool) -> Self:
        """Record whether initialization created the sample project."""
        return replace(self, project_created=1 if created else 0, attempted=True)

    def record_note_created(self) -> Self:
        """Return a result with one additional successfully written note."""
        return replace(self, notes_created=self.notes_created + 1, attempted=True)

    def record_note_failed(self) -> Self:
        """Return a result with one additional note write failure."""
        return replace(self, notes_failed=self.notes_failed + 1, attempted=True)

    def record_index_job_enqueued(self) -> Self:
        """Record the project-index workflow handoff after notes were written."""
        return replace(self, index_jobs_enqueued=1, attempted=True)

    def as_workflow_result(self) -> dict[str, int]:
        """Serialize to the existing workflow result payload shape."""
        if not self.attempted:
            return {}
        return {
            "project_created": self.project_created,
            "notes_created": self.notes_created,
            "notes_failed": self.notes_failed,
            "index_jobs_enqueued": self.index_jobs_enqueued,
        }
