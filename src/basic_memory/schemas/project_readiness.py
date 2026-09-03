"""The readiness contract a caller waits on before trusting a project's reads.

The bug this exists to kill (#1414): ``bm status`` reported a project ready
because it had no pending work, when in fact no work had ever been queued. A
count of outstanding items cannot tell "nothing to do" from "nothing was ever
started", so a waiter polling that count returns a plausible zero instead of an
error. The phase below carries that distinction as data, and every stage carries
its own copy so a caller can wait on the stage it actually depends on.
"""

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import assert_never

from pydantic import BaseModel, Field


class ProjectIndexPhase(StrEnum):
    """What a caller may conclude about a project's index right now.

    Three members, not four. A distinct ``indexing`` and a ``failed`` are not
    observable locally and would be states we can never truthfully report:
    an index pass runs inside whichever process owns the scheduler, so a
    separate ``bm status`` process cannot see one in flight, and the local
    runtime wires no fanout-failure recorder (``LocalProjectIndexRuntime``
    leaves it ``None``), so a failed pass leaves no durable trace to read.
    ``PENDING`` therefore covers both "a pass is running right now" and "a pass
    is owed" — indistinguishable from outside, and identical to a waiter, which
    polls until the work drains either way.
    """

    NEVER_INDEXED = "never_indexed"
    """No index pass has ever completed. Reads cannot be trusted: an empty
    result here means "we never looked", not "there is nothing"."""

    PENDING = "pending"
    """Indexed at least once, and this stage still has outstanding work."""

    IDLE = "idle"
    """Indexed, with nothing outstanding for this stage."""


class ProjectIndexStageName(StrEnum):
    """The independently settleable stages of bringing a project up to date."""

    FILES = "files"
    RELATIONS = "relations"
    EMBEDDINGS = "embeddings"


def combine_index_phases(phases: Iterable[ProjectIndexPhase]) -> ProjectIndexPhase:
    """Reduce per-stage phases to the single phase a caller should act on.

    NEVER_INDEXED dominates (nothing downstream of it can be trusted), then
    PENDING; IDLE only survives when every stage is idle.
    """
    combined = ProjectIndexPhase.IDLE
    for phase in phases:
        match phase:
            case ProjectIndexPhase.NEVER_INDEXED:
                return ProjectIndexPhase.NEVER_INDEXED
            case ProjectIndexPhase.PENDING:
                combined = ProjectIndexPhase.PENDING
            case ProjectIndexPhase.IDLE:
                continue
            case _ as unreachable:  # pragma: no cover - exhaustiveness proof
                assert_never(unreachable)
    return combined


class ProjectIndexStage(BaseModel):
    """One settleable stage, with the phase that says whether waiting will help."""

    name: ProjectIndexStageName = Field(description="Which stage this reports")
    phase: ProjectIndexPhase = Field(description="Whether this stage has settled")
    pending: int = Field(description="Units of work this stage still owes")
    total: int = Field(description="Units of work this stage covers in total")

    @property
    def completed(self) -> int:
        """Units already done — the numerator of a files-done/total progress bar."""
        return max(0, self.total - self.pending)


class ProjectIndexReadiness(BaseModel):
    """Whether a project's index can be trusted, and what it is still owed."""

    phase: ProjectIndexPhase = Field(description="Overall phase across every stage")
    last_indexed_at: datetime | None = Field(
        default=None,
        description="When an index pass last completed; null means none ever has",
    )
    files_on_disk: int = Field(description="Indexable files the project directory currently holds")
    indexed_entities: int = Field(description="Entities currently in the index for this project")
    stages: tuple[ProjectIndexStage, ...] = Field(
        description="Per-stage phases so a caller waits only on what it needs"
    )

    def stage(self, name: ProjectIndexStageName) -> ProjectIndexStage:
        """Return one stage by name.

        Every readiness value carries all three stages, so a missing one is a
        construction bug rather than a caller error — fail loudly instead of
        handing back a fabricated idle stage.
        """
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(f"readiness is missing the {name} stage")  # pragma: no cover

    def describe(self, project_name: str) -> str:
        """Name the state honestly, in the words a human or agent can act on.

        Stated once here so ``bm project add`` and ``bm status`` cannot drift
        into describing the same project differently (#1414).
        """
        files = self.stage(ProjectIndexStageName.FILES)
        match self.phase:
            case ProjectIndexPhase.NEVER_INDEXED:
                if self.files_on_disk == 0:
                    return (
                        f"not yet indexed, no files present — "
                        f"run 'bm project index {project_name}' after adding notes"
                    )
                return (
                    f"{self.files_on_disk} file{'s' if self.files_on_disk != 1 else ''} present, "
                    f"not yet indexed — run 'bm project index {project_name}'"
                )
            case ProjectIndexPhase.PENDING:
                pending_stages = ", ".join(
                    f"{stage.name} {stage.pending}"
                    for stage in self.stages
                    if stage.phase is ProjectIndexPhase.PENDING
                )
                return (
                    f"indexed, {files.completed}/{files.total} files current; "
                    f"pending: {pending_stages}"
                )
            case ProjectIndexPhase.IDLE:
                return (
                    f"indexed and settled — {self.indexed_entities} "
                    f"note{'s' if self.indexed_entities != 1 else ''} "
                    f"from {files.total} file{'s' if files.total != 1 else ''}"
                )
            case _ as unreachable:  # pragma: no cover - exhaustiveness proof
                assert_never(unreachable)
