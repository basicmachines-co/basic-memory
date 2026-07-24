# Basic Memory Codex Plugin Development

This plugin is developed in-place from the Basic Memory repository. Codex installs local plugins
through marketplaces, so local testing uses a repo-local marketplace wrapper rather than publishing
anything external.

## Local Marketplace

The repo marketplace lives at:

```text
.agents/plugins/marketplace.json
```

It exposes this plugin as:

```text
codex@basic-memory
```

The marketplace entry points at `./plugins/codex`, resolved relative to the repository root.

## First-Time Setup

From the repository root:

```bash
codex plugin marketplace add "$(git rev-parse --show-toplevel)"
codex plugin add codex@basic-memory
```

Start a new Codex thread after installing. New threads are the reliable boundary for picking up
plugin skills, hooks, and MCP configuration.

Plugin installation is user-level in Codex, so one install makes the plugin available across
projects on the same machine. Repo-specific memory routing still comes from each checkout's
`.codex/basic-memory.json`.

## Iteration Loop

Pin both hook scripts to the Basic Memory revision under test:

```bash
just set-codex-hook-version "$(git rev-parse origin/main)"
```

After changing files in `plugins/codex`, run the local checks:

```bash
just package-check-codex
```

Then update the manifest cachebuster and reinstall from the local marketplace:

```bash
python3 "$CODEX_PLUGIN_CREATOR_SCRIPTS/update_plugin_cachebuster.py" \
  "$(git rev-parse --show-toplevel)/plugins/codex"
codex plugin add codex@basic-memory
```

Start a fresh Codex thread to test the updated plugin.

## Useful Checks

List configured marketplaces:

```bash
codex plugin marketplace list
```

List plugins Codex can see:

```bash
codex plugin list
```

Run the full package validation gate when touching plugin packaging, shared skills, or integration
metadata:

```bash
just package-check
```

To also run Codex's scaffold validator during `just package-check-codex`, set
`CODEX_PLUGIN_VALIDATOR` to the local `plugin-creator` validator script before
running the check.
