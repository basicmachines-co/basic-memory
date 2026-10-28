"""Version-specific tool surfaces for the v0.24.1 versus 1.0 comparison."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from basic_memory_benchmarks.llm.tool_agent import ToolDef

DECOY_SEARCH_TOOL = ToolDef(
    name="web_search",
    description="Search the public web for information. This server does not store Basic Memory notes.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)


class SurfaceUnavailableError(RuntimeError):
    """The running BM build does not expose the tools this surface needs."""


@dataclass(frozen=True)
class ToolSurface:
    name: str
    # BasicMemoryConfig field -> env value string, applied as
    # BASIC_MEMORY_<FIELD_UPPER>=<value> (BaseSettings env_prefix contract).
    config_overrides: Mapping[str, str]
    # Deterministic order: this is the order tool schemas reach the model.
    tool_allowlist: tuple[str, ...]
    # Human hint for the fail-fast message.
    requires: str


BM_SURFACE = ToolSurface(
    name="bm",
    config_overrides={},
    tool_allowlist=(
        "bm_search",
        "bm_read",
        "bm_list",
        "bm_find",
        "bm_context",
        "bm_man",
        "bm_write",
        "bm_edit",
        "bm_move",
        "bm_delete",
    ),
    requires="a basic-memory checkout with the 1.0 bm_* tools",
)

RICH_SURFACE = ToolSurface(
    name="rich",
    config_overrides={},
    tool_allowlist=(
        "search_notes",
        "read_note",
        "read_content",
        "list_directory",
        "recent_activity",
        "build_context",
        "write_note",
        "edit_note",
        "move_note",
        "delete_note",
    ),
    requires="basic-memory v0.24.1 with the legacy rich tools",
)

POSIX_SURFACE = ToolSurface(
    name="posix",
    config_overrides={"enable_posix_tools": "true"},
    tool_allowlist=(
        "cat",
        "grep",
        "ls",
        "find",
        "tail",
        "man",
        "write_note",
        "edit_note",
        "move_note",
        "delete_note",
    ),
    requires="basic-memory v0.24.1 with the legacy POSIX tools",
)

# Full installed catalogs measure the 27-to-12 consolidation. Alphabetical
# order is explicit on both versions; controlled subsets remain available.
LEGACY_DEFAULT_SURFACE = ToolSurface(
    name="legacy-default",
    config_overrides={},
    tool_allowlist=(
        "basic_memory_diagnostics",
        "build_context",
        "cat",
        "create_memory_project",
        "delete_note",
        "delete_project",
        "edit_note",
        "fetch",
        "find",
        "grep",
        "list_directory",
        "list_memory_projects",
        "list_workspaces",
        "ls",
        "man",
        "move_note",
        "read_content",
        "read_note",
        "recent_activity",
        "schema_diff",
        "schema_infer",
        "schema_validate",
        "search",
        "search_notes",
        "tail",
        "view_note",
        "write_note",
    ),
    requires="basic-memory v0.24.1 default catalog",
)

BM_DEFAULT_SURFACE = ToolSurface(
    name="bm-default",
    config_overrides={},
    tool_allowlist=tuple(sorted((*BM_SURFACE.tool_allowlist, "bm_project", "bm_schema"))),
    requires="basic-memory 1.0 default catalog",
)

SURFACES: dict[str, ToolSurface] = {
    "bm": BM_SURFACE,
    "rich": RICH_SURFACE,
    "posix": POSIX_SURFACE,
    "legacy-default": LEGACY_DEFAULT_SURFACE,
    "bm-default": BM_DEFAULT_SURFACE,
}

SHARED_WRITE_TOOLS = (
    "bm_write",
    "bm_edit",
    "bm_move",
    "bm_delete",
    "write_note",
    "edit_note",
    "move_note",
    "delete_note",
    "create_memory_project",
    "delete_project",
    "bm_project",
    "bm_project_delete",
)


def read_only_view(surface: ToolSurface) -> ToolSurface:
    """The surface with the shared write verbs dropped — symmetrically.

    Dataset-manifest runs reuse one warm project per (surface, group); a write
    from an earlier question would pollute later questions' haystack. Dropping
    mutation verbs from each surface preserves the fairness contract. Grouped
    project commands are omitted because create can mutate the shared fixture.
    """
    return ToolSurface(
        name=surface.name,
        config_overrides=surface.config_overrides,
        tool_allowlist=tuple(
            name for name in surface.tool_allowlist if name not in SHARED_WRITE_TOOLS
        ),
        requires=surface.requires,
    )


def surface_env(surface: ToolSurface, base_env: dict[str, str]) -> dict[str, str]:
    """Apply the surface's config overrides on top of an isolated BM env."""
    env = dict(base_env)
    for setting, value in surface.config_overrides.items():
        env[f"BASIC_MEMORY_{setting.upper()}"] = value
    return env


def verify_surface_tools(
    surface: ToolSurface, available: Iterable[str], *, bm_version: str | None = None
) -> None:
    """Authoritative surface check against the tools the server actually exposes.

    The env override alone is not a check: an old BM's pydantic settings ignore
    the unknown key silently, so only the advertised tool list proves the
    surface exists on this build.
    """
    available_names = set(available)
    missing = [name for name in surface.tool_allowlist if name not in available_names]
    if not missing:
        return
    raise SurfaceUnavailableError(
        f"surface '{surface.name}' needs tools not exposed by this basic-memory "
        f"(bm --version: {bm_version or 'unknown'}): missing {', '.join(missing)}. "
        f"Requires {surface.requires}; point --bm-local-path at it."
    )
