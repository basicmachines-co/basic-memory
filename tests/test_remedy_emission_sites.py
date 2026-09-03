"""Every command this codebase tells a user to run must be able to run (#1440 review).

The readiness formatter that `bm status --cloud` renders hardcoded
`bm project index`, which the local reindex refuses for a cloud project. Earlier
guards for the same property were each scoped to the site that prompted them, so
the next sibling site sat outside the boundary.

The domain here is therefore not a command or a branch but **every place in
`src/` that constructs a runnable command**. Commands are built through
`shell_command` or `command_hint`, so the sites are enumerable: this walks the
source for those calls, recovers the literal command each one spells, and
requires any mode-restricted command to come from a site registered as knowing
the project's mode. A new emission site that names a restricted command fails
here until it is either made mode-aware or explicitly justified.
"""

import ast
from pathlib import Path

import basic_memory

SOURCE_ROOT = Path(basic_memory.__file__).parent

COMMAND_BUILDERS = {"shell_command", "command_hint"}

# Commands that refuse some project modes, and what refuses them.
MODE_RESTRICTED_COMMANDS: dict[tuple[str, ...], str] = {
    ("bm", "project", "index"): "runs the local reindex, which rejects cloud projects",
    ("bm", "reindex"): "runs the local reindex, which rejects cloud projects",
    ("bm", "cloud", "bisync"): "_require_personal_workspace rejects Team workspaces",
    ("bm", "cloud", "sync"): "_require_personal_workspace rejects Team workspaces",
    ("bm", "cloud", "prune"): "_require_personal_workspace rejects Team workspaces",
}

# Sites allowed to build a mode-restricted command, each with why it is safe.
# Keyed by (module relative to basic_memory/, enclosing function) so the registry
# survives line moves.
JUSTIFIED_SITES: dict[tuple[str, str], str] = {
    ("cli/commands/project.py", "add_project"): (
        "the `bm project index` hints sit under `if not effective_cloud_mode`, and the "
        "`bm cloud bisync` one is the Personal-only aside printed after the Team-safe "
        "pull/push steps"
    ),
    ("cli/commands/command_utils.py", "report_project_readiness"): (
        "only reached after a local index pass, so the local command is the one that "
        "can advance this project"
    ),
    ("cli/commands/status.py", "status"): (
        "passes index_command=None when routing is cloud, so describe() says the server "
        "indexes it instead of naming a local command"
    ),
    ("cli/commands/cloud/project_sync.py", "bisync_reset"): (
        "inside `bm cloud bisync-reset`, which calls _require_personal_workspace itself; "
        "the hints repeat that command's own invocation"
    ),
    ("cli/commands/cloud/project_sync.py", "setup_project_sync"): (
        "the Personal-only mirror is offered as an aside after the Team-safe pull/push "
        "next steps, and labelled as such"
    ),
}


def _module_paths() -> list[Path]:
    return sorted(
        path for path in SOURCE_ROOT.rglob("*.py") if "alembic/versions" not in path.as_posix()
    )


def _literal_prefix(call: ast.Call) -> tuple[str, ...]:
    """The leading constant string arguments of a command-builder call."""
    parts: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            parts.append(arg.value)
        else:
            break
    return tuple(parts)


def _enclosing_function(tree: ast.Module, target: ast.Call) -> str:
    """Name of the function containing this call, or "<module>"."""
    best = "<module>"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if inner is target:
                best = node.name
    return best


def _emission_sites() -> list[tuple[str, str, tuple[str, ...]]]:
    """Every constructed command in src, as (module, function, command parts)."""
    sites: list[tuple[str, str, tuple[str, ...]]] = []
    for path in _module_paths():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - source must parse
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name not in COMMAND_BUILDERS:
                continue
            parts = _literal_prefix(node)
            if parts:
                relative = path.relative_to(SOURCE_ROOT).as_posix()
                sites.append((relative, _enclosing_function(tree, node), parts))
    return sites


def test_the_sweep_finds_the_known_emission_sites():
    """Guard the guard: if command building moves, this test must notice.

    A domain check that silently matches nothing would pass forever, which is
    the failure mode of every scoped guard that came before it.
    """
    sites = _emission_sites()

    assert len(sites) >= 10, (
        f"only {len(sites)} constructed commands found; command building probably moved "
        f"away from {COMMAND_BUILDERS} and this sweep no longer covers it"
    )


def test_every_mode_restricted_remedy_is_emitted_from_a_mode_aware_site():
    """The property: never tell a user to run something their project refuses."""
    offenders: list[str] = []

    for module, function, parts in _emission_sites():
        for command, restriction in MODE_RESTRICTED_COMMANDS.items():
            if parts[: len(command)] != command:
                continue
            if (module, function) in JUSTIFIED_SITES:
                continue
            offenders.append(
                f"{module}:{function} emits {' '.join(command)}, which {restriction}. "
                "Make the site mode-aware, or add it to JUSTIFIED_SITES with the reason."
            )

    assert not offenders, "\n".join(offenders)


def test_every_justification_still_describes_a_real_site():
    """A registry entry that no longer matches anything is a stale exemption."""
    live = {(module, function) for module, function, _ in _emission_sites()}
    stale = sorted(site for site in JUSTIFIED_SITES if site not in live)

    assert not stale, f"JUSTIFIED_SITES lists sites that no longer emit commands: {stale}"
