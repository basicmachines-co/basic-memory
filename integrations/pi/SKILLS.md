# Basic Memory skills in the Pi package

The Pi package exposes Pi-aware skills and bundles canonical Basic Memory skills as references.

The active Pi skills are:

- `basic-memory-pi` — use Pi's `bm_recall` and `bm_capture` tools for durable continuity.
- `basic-memory-pi-setup` — create a predictable `.pi/basic-memory.json` with explicit project routing, hook flow, and automation defaults.

The package also bundles these canonical Basic Memory references under `skill-references/*/REFERENCE.md`:

- `memory-notes` — write well-structured Basic Memory notes with observations and relations.
- `memory-capture` — synthesize a working thread into one durable note instead of dumping a transcript.
- `memory-continue` — resume prior work from Basic Memory search, recent activity, and graph context.
- `memory-tasks` — create, track, and resume structured tasks that survive compaction.

These are copied from the monorepo top-level `skills/` source by:

```bash
npm run fetch-skills
```

The generated `skill-references/manifest.json` records the bundled reference skill names and descriptions. Package checks run `fetch-skills` and fail if the generated references are not committed before typechecking and packing.

Pi also supports loading additional Basic Memory skills directly from the top-level skill directory during local development:

```json
{
  "skills": ["/path/to/basic-memory/skills"]
}
```

Use that direct path only for local development when you want raw canonical Basic Memory tool instructions. The published package keeps those files as references and exposes Pi-aware skills so model-facing guidance matches Pi's available tool surface.
