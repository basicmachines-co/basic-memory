"""Transport contract for `bm doctor` project reports (#1798).

A report is a list of findings. Each finding names the check that produced it, how
serious it is, what was observed, and, when one exists, the action that repairs it.
Actions are typed rather than rendered command lines: the API does not know which
client will present them, and a cloud project cannot run a local reindex, so the
client that owns the presentation turns an action into a command.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field


class DoctorSeverity(StrEnum):
    """How much a finding should worry the user.

    Severity follows the consistency model: derived state (search rows, vectors,
    materialization status) catches up on its own, so a lag while work is still
    owed is ``info``. A finding is an ``error`` only when nothing will repair it
    without an action.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DoctorCheck(StrEnum):
    """The checks a project report runs."""

    PROJECT_PATH = "project_path"
    INDEX_READINESS = "index_readiness"
    SEARCH_INDEX = "search_index"
    MATERIALIZATION = "materialization"


class IndexProjectAction(BaseModel):
    """Run the project's first index pass."""

    kind: Literal["index_project"] = "index_project"


class ReindexSearchAction(BaseModel):
    """Rebuild the full-text search index.

    A plain pass re-drives stuck note writes, indexes changed files and purges search
    rows whose note is gone. ``full`` re-indexes every file, which rewrites each
    note's search rows and so also clears duplicate and missing rows.
    """

    kind: Literal["reindex_search"] = "reindex_search"
    full: bool


type DoctorAction = Annotated[
    IndexProjectAction | ReindexSearchAction,
    Field(discriminator="kind"),
]


class DoctorFinding(BaseModel):
    """One observed problem, the evidence for it, and how to fix it if we know."""

    check: DoctorCheck
    severity: DoctorSeverity
    summary: str = Field(description="One sentence describing what was observed")
    evidence: tuple[str, ...] = Field(
        default=(), description="Bounded examples: paths, row ids, error messages"
    )
    action: DoctorAction | None = Field(
        default=None, description="The repair, or null when doctor cannot name one"
    )


class ProjectDoctorReport(BaseModel):
    """Every finding for one project. An empty tuple means the project is healthy."""

    findings: tuple[DoctorFinding, ...]

    @computed_field
    @property
    def has_errors(self) -> bool:
        """Whether any finding needs an action before it will go away."""
        return any(finding.severity is DoctorSeverity.ERROR for finding in self.findings)
