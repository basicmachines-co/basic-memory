---
name: infographics
description: Use when generating Basic Memory PR, changelog, release, or weekly infographics from Codex.
---

# Basic Memory Infographics

Generate repository visuals with evidence-grounded content and canonical output
paths. The file names still say "infographic", but the image may be an
infographic, map, poster, scene, tableau, cover, or other visual form when that
better describes the intent of the PR. PR images are non-gating BM Bossbot
artifacts; changelog and release-summary images are manual evidence-pack
workflows.

## Output Contract

- Base output directory: `docs/assets/infographics/`
- PR infographic: `docs/assets/infographics/pr-<number>.webp`
- Changelog infographic: `docs/assets/infographics/changelog.webp`
- Weekly infographic:
  - This is always a 2-Week Retro window: previous ISO week through current ISO
    week (`start-week = current-week - 1`, `end-week = current-week`).
  - Same year window: `docs/assets/infographics/<year>-w<start-week>-w<end-week>.webp`
  - Cross-year window:
    `docs/assets/infographics/<start-year>-w<start-week>-<end-year>-w<end-week>.webp`

## PR Mode

PR mode uses the BM Bossbot summary block as source material. Do not hand-write
claims that are not present in the PR body.

1. Fetch the PR body:

```bash
gh pr view <number> --json body --jq '.body // ""' > /tmp/bm-pr-body.md
```

2. Generate the canonical asset:

```bash
uv run --script scripts/generate_pr_infographic.py \
  --pr-number <number> \
  --pr-body-file /tmp/bm-pr-body.md \
  --theme "<optional visual theme>" \
  --visual-format auto \
  --provenance-output /tmp/bm-infographic-provenance.md \
  --output docs/assets/infographics/pr-<number>.webp
```

If the PR body contains a managed infographic theme block, the script reads it
automatically:

```markdown
<!-- BM_INFOGRAPHIC_THEME:start -->
<theme>
<!-- BM_INFOGRAPHIC_THEME:end -->
```

Before spending an image call, test the prompt path locally:

```bash
uv run --script scripts/generate_pr_infographic.py \
  --pr-number <number> \
  --pr-body-file /tmp/bm-pr-body.md \
  --theme "<optional visual theme>" \
  --visual-format auto \
  --output docs/assets/infographics/pr-<number>.webp \
  --print-prompt
```

`--dry-run` is an alias for `--print-prompt`; both print the final prompt and
exit without calling OpenAI.

Use `--visual-format auto` by default so the model can choose the strongest
form. Use `--visual-format infographic` when the user wants a structured,
text-forward map/infographic. Use `--visual-format image` when the user wants an
actual editorial scene, movie poster, painting, photograph, tableau, cover, or
symbolic visual moment with minimal text.

When the image is generated, also write provenance with
`--provenance-output <path>`. BM Bossbot publishes that managed block into the
PR body with these markers:

```markdown
<!-- BM_INFOGRAPHIC_PROVENANCE:start -->
...
<!-- BM_INFOGRAPHIC_PROVENANCE:end -->
```

The provenance block records the generated asset path, image model, size,
quality, visual format, theme source, theme or category-choice instruction, and
the exact "Image prompt sent to" the image model. When the Images API provides
a revised prompt, the block records that model interpretation too. Treat this
block as debugging and creative provenance only; it is not a merge gate.

The PR infographic is visual support only. The authoritative merge gate is the
GitHub commit status named `BM Bossbot Approval`.

## Changelog Mode

Build an evidence pack before writing a prompt:

- diff truth source: merged PR diffs, merge commits, or local reconstructed diffs
- changed-file orientation: `git diff --stat` plus key file reads
- impact ledger: before/after outcomes tied to actual changes
- discard list: misleading titles, reverted work, rename-only churn, speculative TODOs
- chosen visual format: infographic, map, poster, scene, tableau, cover, or let
  the model choose
- chosen BM style category: exactly one category from the selection pool below

Read these references before drafting the prompt:

- `references/prompt-blueprint.md`
- `references/style-balance.md`

Read the current `CHANGELOG.md` entries and include the latest meaningful
changes.

## Style And Category Selection

Select exactly one BM style category per infographic based on semantic fit. The
visual language should be recognizable and tasteful, while staying
business-readable.

Also choose the visual form that best communicates the change. Use an
infographic or map when the work has several discrete facts, gates, checks, or
before/after points. Use a poster, scene, tableau, cover image, illustrated
moment, or other image when the PR has a clear intent that is better described
as a visual story. If neither is obvious, let the model choose.

BM category pool:

- computer science college textbooks: SICP-style diagrams, algorithms lectures,
  compiler pipelines, automata, database systems, type theory, operating systems
- classic literature subjects: sea voyages, gothic manors, Dickensian city maps,
  Austen social graphs, library marginalia, travel journals
- fantasy/D&D-inspired: quest maps, dungeon keys, guild ledgers, spellbooks,
  bestiaries, tavern notice boards; no copyrighted settings
- Music: Metal, Hard Rock, Punk, techno, soul, reggae bands; no pop music, no
  direct band logos, album covers, or musician likenesses
- sci-fi: Star Wars inspired knockoff, Spaceballs-adjacent space opera, fleet
  routes, mission consoles, contraband manifests; avoid copyrighted characters,
  logos, or named fictional universes
- Conan the barbarian-inspired sword-and-sorcery: ruined temples, desert routes,
  battle standards, ancient maps; no named character likenesses
- Comic books: issue covers, splash pages, action-panel maps, caption boxes,
  halftone energy, clean sound-effect typography
- French new wave movies: poster style, stark typography, city route maps,
  jump-cut sequencing, high-contrast editorial photography cues
- WWII propaganda posters: home-front public-information poster language,
  logistics arrows, ration charts, mobilization maps, bold simplified figures;
  no real-world party symbols, hate imagery, dehumanizing slogans, or false
  historical claims
- Italian movie posters: hand-painted drama, bold credits, expressive color,
  route-map collage, 1960s or 1970s cinema energy; no direct film titles or
  actor likenesses
- Shakespeare: stage maps, acts and scenes, dramatis personae, royal courts,
  backstage cue sheets
- Greek mythology: temple diagrams, constellation routes, hero's journey maps,
  oracle tablets, labyrinths, ship routes
- noir detective boards: case files, red-string maps, typed evidence labels,
  precinct wall charts
- NASA mission-control dashboards: launch timelines, telemetry maps, orbital
  routes, status boards
- space exploration and astronomy: celestial atlases, observatory charts,
  star-field maps, orbital mechanics diagrams, planetary survey routes,
  telescope annotations, mission trajectories, deep-space timelines
- paintings: abstract painting, classical landscape, Remington-inspired western
  action painting, Rembrandt-inspired chiaroscuro, historical mural, stormy
  seascape, allegorical editorial painting
- classic black-and-white photography: documentary field report, newsroom
  archive print, editorial photo essay, street photography, high-contrast
  darkroom print, contact sheet, civic infrastructure photograph
- 80's action movies: practical explosions, smoky backlit warehouses, neon city
  streets, helicopter searchlights, mission dossiers, heroic silhouettes,
  high-stakes countdowns, painted ensemble posters; no direct actor likenesses,
  real film titles, franchise marks, or catchphrases
- alchemy manuscripts: transformation diagrams, annotated symbols, recipe-like
  process maps, illuminated margins
- brutalist civic planning: transit maps, concrete signage, zoning blocks,
  infrastructure diagrams

Selection rules:

- Pick one category only; do not create mixed mashups.
- Pick the most appropriate visual form; do not force a text-heavy infographic
  when an actual scene, poster, painting, photograph, tableau, or cover would
  communicate the intent better.
- Match metaphor to content, but do not overthink it. The category is a creative
  catalyst, not a semantic constraint.
- Use a polished upscaled editorial rendering direction: smooth anti-aliased
  text, high contrast, clean edges, readable labels.
- Go bold with a map backbone when using an infographic or map. For scene-first
  images, make the category drive the composition through a readable staged
  moment, editorial composition, symbolic environment, route, artifact, or
  visual metaphor.
- Keep the structure literal enough to aid understanding, but not so heavy that
  it obscures engineering meaning.
- Give the image generator creative latitude on layout, structure, color palette,
  and visual metaphors. Be precise about what content to show, loose about how
  to show it.
- Do not use copyrighted characters, logos, or named fictional universes. Use
  genre cues, knockoffs, and original compositions instead.

## Content-First Aesthetic Contract

The meaning must be readable and clearly hierarchical. Everything else is
creative territory: visual format, layout, visual metaphors, decorative
elements, color choices, and category-specific visual language.

Hierarchy:

1. Meaning: what shipped, what changed, and why it matters must be clear.
2. If the image uses text, labels, sections, or evidence bullets, they must be
   legible.
3. The selected category's visual DNA should drive the composition, whether that
   is a readable map structure, a poster, a scene, a tableau, or a symbolic
   object arrangement.
4. Do not play it safe. A visually striking image that someone wants to look at
   beats a correct but boring one.

Hard rules:

- Content sections and labels must be readable when present. Text cannot be
  obscured by decorations.
- Do not use lore-heavy copy that competes with engineering or business meaning.
- Every prompt must include a clear composition cue: map regions, route lines,
  checkpoints, node graphs, a staged scene, a poster composition, a symbolic
  tableau, or a hero object.
- Do not over-prescribe exact coordinates or panel geometry; give a composition
  backbone and let the model compose around it.

## Generation

1. Write the final prompt to a temporary markdown file.
2. Generate with the shared image helper:

```bash
uv run --script scripts/generate_infographic.py \
  --prompt-file /tmp/bm-infographic-prompt.md \
  --output docs/assets/infographics/<name>.webp
```

3. Verify the image exists and is readable before reporting success.

## Quality Bar

- Tell a concrete before/after value story, not vague improvement claims.
- Stay understandable for both engineers and non-technical stakeholders.
- Use plain-language section titles and labels when text is present.
- Include clear visual hierarchy: title, sections, evidence bullets, staged
  focal point, or symbolic scene.
- Avoid invented facts; only use provided source material.
- Favor shipped outcomes over intermediate or reverted work.
- Preserve readability with high contrast, non-tiny labels, and uncluttered
  layout.
