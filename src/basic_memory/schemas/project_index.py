"""Pydantic schemas for project-index route responses."""

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from basic_memory.index import LocalProjectIndexObservation, ProjectIndexCoordinatorResult


class ProjectIndexObservedFileResponse(BaseModel):
    """One project file observed by the project-index runtime."""

    path: str = Field(description="Project-relative file path")
    checksum: str | None = Field(default=None, description="Observed storage checksum")
    size: int | None = Field(default=None, description="Observed storage object size in bytes")


class ProjectIndexStatusResponse(BaseModel):
    """Current project-index observation for a local project."""

    total_files: int = Field(description="Number of files observed for project indexing")
    observed_files: tuple[ProjectIndexObservedFileResponse, ...] = Field(
        description="Files observed by the project-index runtime"
    )

    @classmethod
    def from_observation(
        cls,
        observation: "LocalProjectIndexObservation",
    ) -> "ProjectIndexStatusResponse":
        return cls(
            total_files=observation.total_files,
            observed_files=tuple(
                ProjectIndexObservedFileResponse(
                    path=file.path,
                    checksum=file.checksum,
                    size=file.size,
                )
                for file in observation.observed_files
            ),
        )


class ProjectIndexRunResponse(BaseModel):
    """Summary of one project-index coordinator run."""

    total_files: int = Field(description="Number of files discovered for indexing")
    enqueued_files: int = Field(description="Number of files submitted to child index work")
    enqueued_batches: int = Field(description="Number of child index batches submitted")
    deleted_files: int = Field(description="Number of orphaned indexed files deleted")

    @classmethod
    def from_result(cls, result: "ProjectIndexCoordinatorResult") -> "ProjectIndexRunResponse":
        return cls(
            total_files=result.total_files,
            enqueued_files=result.enqueued_files,
            enqueued_batches=result.enqueued_batches,
            deleted_files=result.deleted_files,
        )
