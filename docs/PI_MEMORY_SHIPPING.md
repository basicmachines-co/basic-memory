# Pi memory package shipping checklist

## Package contents

The Pi package lives at `integrations/pi/` and is intended to publish as `@basicmemory/pi-basic-memory`.

It ships:

- Pi package metadata in `package.json` (`pi.extensions`, `pi.skills`, `pi-package` keyword, public npm publish config).
- TypeScript extension source in `extensions/`.
- Pi-aware active skill in `skills/basic-memory-pi/`.
- Bundled canonical Basic Memory references in `skill-references/*/REFERENCE.md`.
- Skill integration docs in `SKILLS.md`.
- User README in `README.md`.
- AGPL-3.0-or-later license.

The package does not bundle `pi-mcp-adapter`; MCP mode is an opt-in dependency installed separately by the user:

```bash
pi install npm:pi-mcp-adapter
```

## Maintainer checks

From the monorepo root:

```bash
just package-check-pi
```

This runs:

1. `npm ci --ignore-scripts`
2. `npm run fetch-skills`
3. `npm run check-types`
4. `npm test`
5. `npm pack --dry-run`

Run broader package gates before PR/merge:

```bash
just package-check
```

Run Python checks when touching release wiring or CLI behavior:

```bash
just fast-check
```

## Skill refresh

Pi bundles a focused continuity skill set copied from top-level canonical Basic Memory skills:

- `memory-notes`
- `memory-capture`
- `memory-continue`
- `memory-tasks`

Refresh with:

```bash
cd integrations/pi
npm run fetch-skills
```

The generated `skill-references/manifest.json` should be committed with the bundled `REFERENCE.md` files.

## Manual E2E before shipping

Use isolated temp state. Do not mutate live Basic Memory projects or live Pi config.

Minimum scenarios:

1. CLI capture to Basic Memory, then fresh Pi session CLI recall.
2. CLI-written note recalled through `pi-mcp-adapter` MCP mode.
3. MCP adapter `write_note`, then CLI recall/search finds the MCP-written note.
4. Missing adapter in MCP mode reports a warning and leaves Pi usable.
5. Missing or broken `bmPath` reports a visible capture/recall failure and does not claim persistence.

Latest manual evidence: `docs/PI_MEMORY_E2E_RESULTS.md`.

## Release wiring

`just set-version ... --scope packages` now updates:

- `integrations/pi/package.json`
- `integrations/pi/package-lock.json`

Stable/beta release recipes add those files to the release bump commit.

## Publishing

The Python package release workflow currently publishes PyPI, OpenClaw npm, GitHub release, and Homebrew artifacts. Pi npm publishing still needs workflow wiring before it is automatic.

Open release tasks before declaring this shippable:

- Add GitHub Actions publishing for `integrations/pi` to npm using the same version/tag policy as OpenClaw.
- Decide whether Pi should publish only on stable tags or on beta tags too.
- Add a docs.basicmemory.com Pi integration page in the docs repo.
- Add package gallery metadata (`image` or `video`) if desired before public announcement.
