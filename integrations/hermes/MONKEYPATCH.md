# Hermes slash-command monkeypatch

This document describes the Hermes Agent-side patch needed when `/bm-*` slash commands do not appear in the gateway even though the Basic Memory provider itself is installed and working.

This issue is tracked upstream in https://github.com/NousResearch/hermes-agent/issues/23603

Agents may use this file as a guide to add support for `/bm-*` slash commands in Hermes Agent builds that do not yet include the upstream fix.

## Current status and compatibility

This is a **Hermes Agent-side** compatibility patch, not a Basic Memory plugin runtime patch.

| Hermes Agent version | Basic Memory plugin version | Guidance |
|---|---:|---|
| `v0.13.x` | `v0.3.0` | Plugin `v0.3.0` remains the right runtime release. If `/bm-*` commands are missing, use the Hermes Agent-side monkeypatch below or wait for the upstream Hermes fix. |
| `v0.14.0` / `v2026.5.16` | `v0.3.1` docs, runtime still equivalent to `v0.3.0` | The plugin runtime still works, but Hermes Agent `v0.14.0` still does **not** include the upstream slash-command discovery fix. Use the v0.14.0-compatible Hermes Agent-side patch below. |
| Future Hermes release with upstream fix | Latest plugin | Do **not** apply this monkeypatch unless `/bm-*` commands are still absent; the fix should be redundant once Hermes loads active exclusive memory-provider commands during command discovery. |

Checked against Hermes Agent `v2026.5.16` / `v0.14.0` on 2026-05-16: the upstream Hermes release still does **not** include this fix. After applying the Hermes Agent-side patch below locally, `get_plugin_commands()` returns the expected `/bm-*` commands.

Important nuance: recent `hermes-basic-memory` versions include a best-effort PluginManager reach-in that registers commands when the provider is loaded. That workaround alone is not enough for gateway startup discovery in affected Hermes builds, because `get_plugin_commands()` does not load the active exclusive memory provider. The Hermes Agent-side patch is still needed until upstream command discovery loads the active memory provider and the memory-provider collector delegates command/skill registration.

Release/tagging note for agents: `v0.3.1` is a documentation release that clarifies Hermes Agent `v0.14.0` compatibility instructions. It does not require users on Hermes Agent `v0.13.x` to change plugin runtime behavior, and it should not be interpreted as a Basic Memory data/schema migration.

## Problem

`hermes-basic-memory` is an **exclusive memory-provider plugin**. Hermes loads exclusive memory providers through `plugins.memory`, not through the normal `PluginManager` discovery path.

Gateway adapters register native slash commands during startup by calling Hermes's plugin command discovery APIs. In affected Hermes builds, that startup path only sees commands registered by normal plugins. The active memory provider has not been loaded yet, and the memory-provider loader uses a collector that captures only `register_memory_provider(...)`. As a result, commands registered by this plugin with `ctx.register_command(...)` never reach the central plugin command registry before Discord/native slash-command sync.

Symptoms:

- `hermes memory status` shows `Provider: basic-memory` and `Status: available`.
- Agent tools such as `bm_search`, `bm_read`, and `bm_recent` work.
- Native slash commands such as `/bm-search`, `/bm-read`, and `/bm-context` are missing after `hermes gateway restart`.

## Target behavior

When Hermes builds its plugin command list, it should also load the configured active memory provider once, allowing that provider to register commands and skills into the same central registries used by ordinary plugins.

After the patch, `get_plugin_commands()` should include commands such as:

```text
bm-context
bm-project
bm-read
bm-recent
bm-remember
bm-search
bm-status
bm-workspace
```

## Files to patch in Hermes Agent

Patch these files in the Hermes Agent repository, not in this plugin repository:

```text
hermes_cli/plugins.py
plugins/memory/__init__.py
```

Recommended tests to add/update in Hermes Agent:

```text
tests/hermes_cli/test_plugin_cli_registration.py
tests/hermes_cli/test_plugins.py
```

## Implementation outline

### 1. Load active memory-provider commands from `get_plugin_commands()`

In `hermes_cli/plugins.py`, add module-level idempotency/recursion guards near the global plugin manager:

```python
_plugin_manager: Optional[PluginManager] = None
_memory_provider_command_loads: set[str] = set()
_memory_provider_command_loading = False
```

Update `get_plugin_commands()` so it first ensures normal plugin discovery, then best-effort loads the active memory provider before returning the command registry:

```python
def get_plugin_commands() -> Dict[str, dict]:
    """Return the full plugin commands dict (name → {handler, description, plugin}).

    Triggers idempotent plugin discovery so callers can use plugin commands
    before any explicit discover_plugins() call. Also initializes the active
    memory provider once so exclusive memory-provider plugins can contribute
    gateway slash commands during startup discovery.
    """
    manager = _ensure_plugins_discovered()
    _ensure_active_memory_provider_commands_loaded()
    return manager._plugin_commands
```

Add the helper:

```python
def _ensure_active_memory_provider_commands_loaded() -> None:
    """Best-effort load of the active memory provider's slash commands."""
    global _memory_provider_command_loading
    if _memory_provider_command_loading:
        return
    try:
        from plugins import memory as memory_plugins

        active = memory_plugins._get_active_memory_provider()
        if not active or active in _memory_provider_command_loads:
            return
        _memory_provider_command_loading = True
        try:
            memory_plugins.load_memory_provider(active)
            _memory_provider_command_loads.add(active)
        finally:
            _memory_provider_command_loading = False
    except Exception as exc:
        logger.debug(
            "Failed to load active memory-provider plugin commands: %s",
            exc,
            exc_info=_PLUGINS_DEBUG,
        )
```

Notes:

- This must be best-effort; command discovery should not break Hermes startup if a memory provider is misconfigured.
- The recursion guard prevents `load_memory_provider(...)` → provider `register(...)` → `ctx.register_command(...)` → plugin manager access from re-entering endlessly.
- The load set prevents duplicate provider command registration work.

### 2. Make the memory-provider collector delegate commands and skills

In `plugins/memory/__init__.py`, import `Callable`:

```python
from typing import Callable, List, Optional, Tuple
```

When loading a provider directory, pass the plugin/provider name to the collector:

```python
collector = _ProviderCollector(plugin_name=name)
```

Replace the collector that only captures `register_memory_provider(...)` with a plugin-context shim that also delegates `register_command(...)` and `register_skill(...)` into the central `PluginManager` registries:

```python
class _ProviderCollector:
    """Plugin-context shim used while loading memory providers.

    Memory providers are exclusive plugins and are loaded by this module
    instead of the general PluginManager. They still need access to the same
    slash-command and skill registries as normal plugins, otherwise active
    memory-provider commands are invisible during gateway startup discovery.
    """

    def __init__(self, plugin_name: str = "memory-provider"):
        self.provider = None
        self.plugin_name = plugin_name

    def register_memory_provider(self, provider):
        self.provider = provider

    def register_command(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        args_hint: str = "",
    ) -> None:
        """Register a memory-provider slash command with PluginManager."""
        try:
            from hermes_cli.plugins import _ensure_plugins_discovered
        except Exception:
            return

        clean = name.lower().strip().lstrip("/").replace(" ", "-")
        if not clean:
            return

        try:
            manager = _ensure_plugins_discovered()
        except Exception:
            return

        plugin_commands = getattr(manager, "_plugin_commands", None)
        if plugin_commands is None:
            return
        plugin_commands[clean] = {
            "handler": handler,
            "description": description or "Plugin command",
            "plugin": self.plugin_name,
            "args_hint": (args_hint or "").strip(),
        }

    def register_skill(
        self,
        name: str,
        path: Path,
        description: str = "",
    ) -> None:
        """Register a memory-provider skill with PluginManager."""
        try:
            from agent.skill_utils import _NAMESPACE_RE
            from hermes_cli.plugins import _ensure_plugins_discovered
        except Exception:
            return

        if ":" in name or not name or not _NAMESPACE_RE.match(name):
            raise ValueError(f"Invalid skill name '{name}'.")
        if not path.exists():
            raise FileNotFoundError(f"SKILL.md not found at {path}")

        try:
            manager = _ensure_plugins_discovered()
        except Exception:
            return

        plugin_skills = getattr(manager, "_plugin_skills", None)
        if plugin_skills is None:
            return
        plugin_skills[f"{self.plugin_name}:{name}"] = {
            "path": path,
            "plugin": self.plugin_name,
            "bare_name": name,
            "description": description,
        }
```

Keep existing no-op methods such as `register_tool(...)` and `register_cli_command(...)` as no-ops unless the target Hermes version expects otherwise.

## Verification

From the Hermes Agent repository, run focused compile/tests:

```bash
python -m py_compile hermes_cli/plugins.py plugins/memory/__init__.py
python -m pytest \
  tests/hermes_cli/test_plugins.py::TestPluginCommands::test_get_plugin_commands_loads_active_memory_provider_commands \
  tests/hermes_cli/test_plugin_cli_registration.py::TestProviderCollectorRegistration \
  -q -o 'addopts='
```

Then verify the active config sees the Basic Memory commands:

```bash
python - <<'PY'
import hermes_cli.plugins as p
p._plugin_manager = None
p._memory_provider_command_loads.clear()
cmds = p.get_plugin_commands()
print(sorted(k for k in cmds if k.startswith('bm-')))
PY
```

Expected output:

```text
['bm-context', 'bm-project', 'bm-read', 'bm-recent', 'bm-remember', 'bm-search', 'bm-status', 'bm-workspace']
```

Finally restart the gateway so native slash commands are synced:

```bash
hermes gateway restart
```

For Discord, global command propagation can lag briefly. If the commands do not show immediately, type `/bm` directly or reload the Discord client.
