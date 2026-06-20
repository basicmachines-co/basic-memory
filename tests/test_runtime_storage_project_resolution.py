"""Tests for portable storage-prefix project resolution."""

from dataclasses import FrozenInstanceError, dataclass

import pytest

from basic_memory.runtime import (
    StorageProjectPrefixMatch,
    resolve_storage_project_prefix,
    storage_object_key_from_project_path,
    storage_object_key_from_project_prefix,
    storage_project_prefix_from_project_path,
)


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    name: str
    path: str
    is_active: bool = True


def test_storage_project_prefix_from_project_path_strips_legacy_mount_prefix() -> None:
    assert storage_project_prefix_from_project_path("/app/data/basic-memory") == "basic-memory"
    assert storage_project_prefix_from_project_path("/basic-memory") == "basic-memory"
    assert storage_project_prefix_from_project_path("basic-memory") == "basic-memory"


def test_storage_object_key_from_project_prefix_joins_project_relative_path() -> None:
    assert storage_object_key_from_project_prefix("basic-memory", "notes/a.md") == (
        "basic-memory/notes/a.md"
    )
    assert storage_object_key_from_project_prefix("/basic-memory/", "/notes/a.md") == (
        "basic-memory/notes/a.md"
    )
    assert storage_object_key_from_project_prefix("basic-memory", "notes/") == "basic-memory/notes/"
    assert storage_object_key_from_project_prefix("", "/notes/a.md") == "notes/a.md"
    assert storage_object_key_from_project_prefix("basic-memory", "") == "basic-memory/"


def test_storage_object_key_from_project_path_strips_legacy_project_mount() -> None:
    assert storage_object_key_from_project_path("/app/data/basic-memory", "notes/a.md") == (
        "basic-memory/notes/a.md"
    )


def test_storage_project_prefix_resolution_prefers_exact_active_path() -> None:
    exact = Project(id=1, name="Main", path="bucket-prefix")
    name_match = Project(id=2, name="bucket-prefix", path="other")

    result = resolve_storage_project_prefix(
        "bucket-prefix",
        exact_path_project=exact,
        name_project=name_match,
        active_projects=(name_match,),
    )

    assert result.project is exact
    assert result.match == StorageProjectPrefixMatch.exact_path
    assert result.matched
    with pytest.raises(FrozenInstanceError):
        setattr(result, "match", StorageProjectPrefixMatch.name)


def test_storage_project_prefix_resolution_uses_name_then_unique_path_suffix() -> None:
    inactive_exact = Project(id=1, name="Inactive", path="bucket-prefix", is_active=False)
    name_match = Project(id=2, name="bucket-prefix", path="different")
    suffix_match = Project(id=3, name="Main", path="/app/data/bucket-prefix")

    name_result = resolve_storage_project_prefix(
        "bucket-prefix",
        exact_path_project=inactive_exact,
        name_project=name_match,
        active_projects=(suffix_match,),
    )
    suffix_result = resolve_storage_project_prefix(
        "bucket-prefix",
        exact_path_project=inactive_exact,
        name_project=None,
        active_projects=(suffix_match,),
    )

    assert name_result.project is name_match
    assert name_result.match == StorageProjectPrefixMatch.name
    assert suffix_result.project is suffix_match
    assert suffix_result.match == StorageProjectPrefixMatch.path_suffix


def test_storage_project_prefix_resolution_reports_ambiguous_suffix_matches() -> None:
    left = Project(id=1, name="Left", path="/app/data/shared")
    right = Project(id=2, name="Right", path="/mnt/data/shared")

    result = resolve_storage_project_prefix(
        "shared",
        exact_path_project=None,
        name_project=None,
        active_projects=(left, right),
    )

    assert result.project is None
    assert result.match == StorageProjectPrefixMatch.ambiguous_path_suffix
    assert result.suffix_matches == (left, right)
    assert not result.matched


def test_storage_project_prefix_resolution_reports_missing_project() -> None:
    available = Project(id=1, name="Other", path="/app/data/other")

    result = resolve_storage_project_prefix(
        "missing",
        exact_path_project=None,
        name_project=None,
        active_projects=(available,),
    )

    assert result.project is None
    assert result.match == StorageProjectPrefixMatch.missing
    assert result.available_projects == (available,)
    assert not result.matched
