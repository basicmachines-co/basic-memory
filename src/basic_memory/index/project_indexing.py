"""Runtime-neutral route contracts for project indexing."""

from dataclasses import dataclass
from typing import Protocol

from basic_memory.indexing.project_index_coordinator import ProjectIndexCoordinatorResult
from basic_memory.runtime.jobs import RuntimeObservedIndexFile
from basic_memory.schemas.v2.project_index import ProjectIndexResponse


@dataclass(frozen=True, slots=True)
class ProjectIndexObservation:
    """Files visible to the active project-index runtime."""

    observed_files: tuple[RuntimeObservedIndexFile, ...]

    @property
    def total_files(self) -> int:
        return len(self.observed_files)


class ProjectIndexRunner(Protocol):
    async def index_project(
        self,
        project_id: int,
        *,
        force_full: bool = False,
    ) -> ProjectIndexCoordinatorResult: ...


class ProjectIndexObserver(Protocol):
    async def observe_project(self, project_id: int) -> ProjectIndexObservation: ...


class ProjectIndexScheduler(Protocol):
    def schedule_project_index(self, *, project_id: int, force_full: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectIndexRouteRequest:
    project_id: int
    project_name: str
    force_full: bool
    run_in_background: bool


class ProjectIndexCommand(Protocol):
    async def index_project(self, request: ProjectIndexRouteRequest) -> ProjectIndexResponse: ...
