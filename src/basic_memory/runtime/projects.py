"""Portable project identity contracts for Basic Memory runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Self

from basic_memory.runtime.storage import (
    ProjectExternalId,
    ProjectId,
    ProjectName,
    ProjectPath,
    ProjectPermalink,
)


class ProjectRuntimeSource(Protocol):
    """Minimal project shape needed by worker/runtime code."""

    @property
    def id(self) -> ProjectId: ...

    @property
    def external_id(self) -> object | None: ...

    @property
    def path(self) -> object | None: ...

    @property
    def name(self) -> object | None: ...

    @property
    def permalink(self) -> object | None: ...


@dataclass(frozen=True, slots=True)
class ProjectRuntimeReference:
    """Stable project identity used by workers and storage events."""

    project_id: ProjectId
    project_external_id: ProjectExternalId
    project_path: ProjectPath
    project_name: ProjectName | None = None
    project_permalink: ProjectPermalink | None = None

    @classmethod
    def from_project(cls, project: ProjectRuntimeSource) -> Self:
        project_external_id = str(project.external_id).strip() if project.external_id else ""
        if not project_external_id:
            raise ValueError(f"Project {project.id} is missing external_id")

        project_path = str(project.path).strip() if project.path else ""
        if not project_path:
            raise ValueError(f"Project {project.id} is missing path")

        project_name = str(project.name).strip() if project.name else None
        project_permalink = str(project.permalink).strip() if project.permalink else None

        return cls(
            project_id=project.id,
            project_external_id=project_external_id,
            project_path=project_path,
            project_name=project_name,
            project_permalink=project_permalink,
        )

    def require_project_name(self) -> ProjectName:
        if not self.project_name:
            raise RuntimeError(f"Project {self.project_id} is missing name")
        return self.project_name

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize project identity for existing workflow metadata contracts."""
        return {
            "project_id": self.project_id,
            "project_external_id": self.project_external_id,
            "project_name": self.project_name,
            "project_permalink": self.project_permalink,
            "project_path": self.project_path,
        }
