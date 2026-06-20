"""Resolve storage object prefixes to Basic Memory projects."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from posixpath import join as posix_join
from typing import Protocol

from basic_memory.runtime.contracts import ProjectId, ProjectPath, StorageKey


class StorageProjectSource(Protocol):
    """Minimal project shape needed for storage-prefix routing."""

    @property
    def id(self) -> ProjectId: ...

    @property
    def name(self) -> object | None: ...

    @property
    def path(self) -> object | None: ...

    @property
    def is_active(self) -> bool: ...


class StorageProjectPrefixMatch(StrEnum):
    """How a storage prefix resolved to a project."""

    exact_path = "exact_path"
    name = "name"
    path_suffix = "path_suffix"
    ambiguous_path_suffix = "ambiguous_path_suffix"
    missing = "missing"


@dataclass(frozen=True, slots=True)
class StorageProjectPrefixResolution[ProjectT: StorageProjectSource]:
    """Project resolution result for one storage object key prefix."""

    bucket_prefix: ProjectPath
    match: StorageProjectPrefixMatch
    project: ProjectT | None = None
    suffix_matches: tuple[ProjectT, ...] = ()
    available_projects: tuple[ProjectT, ...] = ()

    @property
    def matched(self) -> bool:
        return self.project is not None


def storage_project_prefix_from_project_path(project_path: ProjectPath) -> ProjectPath:
    """Normalize a Basic Memory project path into a storage object-key prefix."""
    normalized_path = project_path
    if normalized_path.startswith("/app/data/"):
        normalized_path = normalized_path.removeprefix("/app/data/")
    return normalized_path.lstrip("/")


def storage_object_key_from_project_prefix(
    project_prefix: ProjectPath,
    relative_path: str,
) -> StorageKey:
    """Join a normalized project prefix and project-relative path into a storage key."""
    normalized_prefix = project_prefix.strip("/")
    normalized_relative_path = relative_path.lstrip("/")
    if not normalized_relative_path:
        return f"{normalized_prefix}/" if normalized_prefix else ""
    if not normalized_prefix:
        return normalized_relative_path
    return posix_join(normalized_prefix, normalized_relative_path)


def storage_object_key_from_project_path(
    project_path: ProjectPath,
    relative_path: str,
) -> StorageKey:
    """Build a storage key from a Basic Memory project path and relative file path."""
    return storage_object_key_from_project_prefix(
        storage_project_prefix_from_project_path(project_path),
        relative_path,
    )


def resolve_storage_project_prefix[ProjectT: StorageProjectSource](
    bucket_prefix: ProjectPath,
    *,
    exact_path_project: ProjectT | None,
    name_project: ProjectT | None,
    active_projects: Sequence[ProjectT],
) -> StorageProjectPrefixResolution[ProjectT]:
    """Resolve a bucket object-key prefix using cloud-compatible project matching."""
    normalized_prefix = bucket_prefix.strip()
    active_project_tuple = tuple(project for project in active_projects if project.is_active)

    if exact_path_project is not None and exact_path_project.is_active:
        return StorageProjectPrefixResolution(
            bucket_prefix=normalized_prefix,
            match=StorageProjectPrefixMatch.exact_path,
            project=exact_path_project,
            available_projects=active_project_tuple,
        )

    if name_project is not None and name_project.is_active:
        return StorageProjectPrefixResolution(
            bucket_prefix=normalized_prefix,
            match=StorageProjectPrefixMatch.name,
            project=name_project,
            available_projects=active_project_tuple,
        )

    suffix_matches = tuple(
        project
        for project in active_project_tuple
        if Path(str(project.path or "")).name == normalized_prefix
    )
    if len(suffix_matches) == 1:
        return StorageProjectPrefixResolution(
            bucket_prefix=normalized_prefix,
            match=StorageProjectPrefixMatch.path_suffix,
            project=suffix_matches[0],
            suffix_matches=suffix_matches,
            available_projects=active_project_tuple,
        )

    if len(suffix_matches) > 1:
        return StorageProjectPrefixResolution(
            bucket_prefix=normalized_prefix,
            match=StorageProjectPrefixMatch.ambiguous_path_suffix,
            suffix_matches=suffix_matches,
            available_projects=active_project_tuple,
        )

    return StorageProjectPrefixResolution(
        bucket_prefix=normalized_prefix,
        match=StorageProjectPrefixMatch.missing,
        available_projects=active_project_tuple,
    )
