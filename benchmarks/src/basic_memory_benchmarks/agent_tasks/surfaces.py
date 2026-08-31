"""Tool surfaces as data: rich (today's MCP tools) vs posix (#1399/#1406).

This branch does NOT contain the posix tools — they live on the 1399/1403
stack — so the surface is pure data: config overrides applied to the ephemeral
BM instance's environment plus a tool allowlist. Selecting the posix surface
against a BM build that lacks the flag fails fast (or is recorded as an
explicit skip) via ``verify_surface_tools``; the six posix tool names below
are the single place to reconcile against that branch if names drift.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


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
    requires="any current basic-memory build",
)

# Read verbs swapped per #1399; write verbs shared — posix v1 is read-side
# only ("later issue if the read-side A/B justifies it"), so the A/B isolates
# the read-side surface while every task stays runnable on both surfaces.
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
    requires="a basic-memory checkout with the enable_posix_tools flag (#1399/#1406 stack)",
)

SURFACES: dict[str, ToolSurface] = {"rich": RICH_SURFACE, "posix": POSIX_SURFACE}

# The write verbs both surfaces share (posix v1 is read-side only, see above).
SHARED_WRITE_TOOLS = ("write_note", "edit_note", "move_note", "delete_note")


def read_only_view(surface: ToolSurface) -> ToolSurface:
    """The surface with the shared write verbs dropped — symmetrically.

    Dataset-manifest runs reuse one warm project per (surface, group); a write
    from an earlier question would pollute later questions' haystack. Dropping
    the same four verbs from BOTH surfaces preserves the fairness contract and
    matches xAFS's read-only reference agent (grep/find/cat).
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
