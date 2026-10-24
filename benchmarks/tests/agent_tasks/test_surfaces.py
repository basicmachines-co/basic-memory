"""The runnable surface must require the new tools and exclude mutation in read-only runs."""

import pytest
from basic_memory_benchmarks.agent_tasks.surfaces import (
    BM_SURFACE,
    SURFACES,
    SHARED_WRITE_TOOLS,
    ToolSurface,
    SurfaceUnavailableError,
    read_only_view,
    surface_env,
    verify_surface_tools,
)


def test_registry_requires_1_0_tools():
    assert SURFACES == {"bm": BM_SURFACE}
    assert all(name.startswith("bm_") for name in BM_SURFACE.tool_allowlist)
    verify_surface_tools(BM_SURFACE, BM_SURFACE.tool_allowlist)
    with pytest.raises(SurfaceUnavailableError, match="bm_search"):
        verify_surface_tools(BM_SURFACE, ["search_notes", "read_note"])


def test_read_only_surface_excludes_mutation():
    view = read_only_view(BM_SURFACE)
    assert view.name == BM_SURFACE.name
    assert view.config_overrides == BM_SURFACE.config_overrides
    assert not set(SHARED_WRITE_TOOLS) & set(view.tool_allowlist)
    assert "bm_project" not in view.tool_allowlist  # its create command writes
    assert "bm_schema" not in view.tool_allowlist
    assert "bm_read" in view.tool_allowlist


def test_surface_env_preserves_input():
    surface = ToolSurface("test", {"example": "true"}, (), "test fixture")
    base = {"EXISTING": "1"}
    assert surface_env(surface, base) == {"EXISTING": "1", "BASIC_MEMORY_EXAMPLE": "true"}
    assert base == {"EXISTING": "1"}
