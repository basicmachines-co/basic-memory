"""Tests for tool-surface definitions and the fail-fast availability check."""

from __future__ import annotations

import pytest

from basic_memory_benchmarks.agent_tasks.surfaces import (
    POSIX_SURFACE,
    RICH_SURFACE,
    SURFACES,
    SurfaceUnavailableError,
    surface_env,
    verify_surface_tools,
)

POSIX_READ_TOOLS = ("cat", "grep", "ls", "find", "tail", "man")
SHARED_WRITE_TOOLS = ("write_note", "edit_note", "move_note", "delete_note")


def test_registry_names_both_surfaces() -> None:
    assert set(SURFACES) == {"rich", "posix"}
    assert SURFACES["rich"] is RICH_SURFACE
    assert SURFACES["posix"] is POSIX_SURFACE


def test_posix_allowlist_is_read_swap_plus_shared_write_verbs() -> None:
    # Posix v1 (#1399) is read-side only: the six read tools replace the rich
    # read verbs; the write verbs are shared with the rich surface.
    assert POSIX_SURFACE.tool_allowlist == POSIX_READ_TOOLS + SHARED_WRITE_TOOLS
    assert RICH_SURFACE.tool_allowlist[-4:] == SHARED_WRITE_TOOLS


def test_rich_surface_needs_no_config_overrides() -> None:
    assert RICH_SURFACE.config_overrides == {}


def test_surface_env_maps_flag_to_basic_memory_env_var() -> None:
    env = surface_env(POSIX_SURFACE, {"EXISTING": "1"})
    assert env["BASIC_MEMORY_ENABLE_POSIX_TOOLS"] == "true"
    assert env["EXISTING"] == "1"


def test_surface_env_does_not_mutate_base_env() -> None:
    base = {"EXISTING": "1"}
    surface_env(POSIX_SURFACE, base)
    assert base == {"EXISTING": "1"}


def test_verify_passes_on_superset() -> None:
    available = set(POSIX_SURFACE.tool_allowlist) | {"extra_tool"}
    verify_surface_tools(POSIX_SURFACE, available)


def test_verify_rich_surface_through_same_path() -> None:
    verify_surface_tools(RICH_SURFACE, RICH_SURFACE.tool_allowlist)
    with pytest.raises(SurfaceUnavailableError):
        verify_surface_tools(RICH_SURFACE, ["write_note"])


def test_verify_missing_posix_tools_raises_with_guidance() -> None:
    # A rich-only BM (this branch) exposes the write verbs but no posix reads.
    with pytest.raises(SurfaceUnavailableError) as excinfo:
        verify_surface_tools(POSIX_SURFACE, SHARED_WRITE_TOOLS, bm_version="0.23.2")
    message = str(excinfo.value)
    assert "surface 'posix'" in message
    assert "cat, grep, ls, find, tail, man" in message
    assert "enable_posix_tools" in message
    assert "0.23.2" in message
    assert "--bm-local-path" in message
