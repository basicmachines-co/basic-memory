"""The 1.0 tool surface, pinned in each run manifest.

Historical rich/POSIX artifacts retain their original names and allowlists.
New runs use bm; legacy presets are retired rather than silently relabeled.
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

SURFACES: dict[str, ToolSurface] = {"bm": BM_SURFACE}

SHARED_WRITE_TOOLS = ("bm_write", "bm_edit", "bm_move", "bm_delete")


def read_only_view(surface: ToolSurface) -> ToolSurface:
    """The surface with the shared write verbs dropped — symmetrically.

    Dataset-manifest runs reuse one warm project per (surface, group); a write
    from an earlier question would pollute later questions' haystack. Dropping
    the same four verbs from each surface preserves the fairness contract and
    matches xAFS's read-only reference agent.
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
