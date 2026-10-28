"""The runnable surface must require the new tools and exclude mutation in read-only runs."""

import pytest
from basic_memory_benchmarks.agent_tasks.surfaces import (
    BM_SURFACE,
    RICH_SURFACE,
    POSIX_SURFACE,
    SURFACES,
    SHARED_WRITE_TOOLS,
    ToolSurface,
    SurfaceUnavailableError,
    read_only_view,
    surface_env,
    verify_surface_tools,
)


def test_registry_requires_1_0_tools():
    assert SURFACES == {"bm": BM_SURFACE, "rich": RICH_SURFACE, "posix": POSIX_SURFACE}
    assert all(name.startswith("bm_") for name in BM_SURFACE.tool_allowlist)
    verify_surface_tools(BM_SURFACE, BM_SURFACE.tool_allowlist)
    with pytest.raises(SurfaceUnavailableError, match="bm_search"):
        verify_surface_tools(BM_SURFACE, ["search_notes", "read_note"])


@pytest.mark.parametrize("surface", [BM_SURFACE, RICH_SURFACE, POSIX_SURFACE])
def test_read_only_surface_excludes_mutation(surface):
    view = read_only_view(surface)
    assert view.name == surface.name
    assert view.config_overrides == surface.config_overrides
    assert not set(SHARED_WRITE_TOOLS) & set(view.tool_allowlist)
    assert "bm_project" not in view.tool_allowlist  # its create command writes
    assert "bm_schema" not in view.tool_allowlist
    assert len(view.tool_allowlist) == len(surface.tool_allowlist) - 4


def test_legacy_surface_cannot_run_against_1_0():
    for surface in (RICH_SURFACE, POSIX_SURFACE):
        verify_surface_tools(surface, surface.tool_allowlist)
        with pytest.raises(SurfaceUnavailableError, match="v0.24.1"):
            verify_surface_tools(surface, BM_SURFACE.tool_allowlist)


def test_surface_env_preserves_input():
    surface = ToolSurface("test", {"example": "true"}, (), "test fixture")
    base = {"EXISTING": "1"}
    assert surface_env(surface, base) == {"EXISTING": "1", "BASIC_MEMORY_EXAMPLE": "true"}
    assert base == {"EXISTING": "1"}
