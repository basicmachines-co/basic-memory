# Prompt Blueprint

Convert an evidence pack into a final visual prompt. Be precise about the
content and loose about visual execution.

## Required Inputs

- Diff truth source summary
- Changed-file orientation summary
- Impact ledger with before/after outcomes
- Discard list for excluded noise
- Chosen image form
- Chosen BM style category

## Prompt Shape

```text
Create a polished Basic Memory editorial image inspired by
<BM_STYLE_CATEGORY>. Use a poster, scene, tableau, painting, photograph, cover
image, staged artifact, or another image-first form that best communicates the
intent. Use HD editorial rendering with smooth anti-aliased text when text is
present. Go bold and let the selected category drive the visual language through
original, non-infringing cues.

TITLE:
- "<clear title>"
- "<scope subtitle>"

COMPOSITION:
- Recreate a clear staged moment or symbolic image that describes the PR
  intent.
- Maps, diagrams, dossiers, route lines, labels, and artifacts can appear as
  props inside the scene, but the output should read as an image rather than a
  dense infographic.
- Take creative liberty with layout and styling.
- The hard rule: the meaning must be readable and clearly hierarchical.
- Keep labels plain-language and technical when labels are used.

CONTENT:
1. "<section>"
  - <evidence-grounded outcome>
  - <evidence-grounded outcome>

2. "<section>"
  - <evidence-grounded outcome>
  - <evidence-grounded outcome>

METRICS:
- <metric>
- <metric>

STYLE DIRECTION:
- Upscaled editorial, high contrast, anti-aliased text, smooth edges.
- Let the category's visual DNA drive the composition.
- Use genre/category cues only; do not use copyrighted characters, logos, named
  fictional universes, direct band logos, album art, or celebrity likenesses.

DO NOT:
- Make text unreadable or let decoration obscure content.
- Render a text-heavy infographic, dashboard, flowchart, timeline strip,
  checklist, bullet-list panel, or dense explanatory diagram.
- Use crunchy low-resolution pixel art.
- Invent facts not present in the evidence pack.
```

## Writing Rules

- Keep each bullet specific and evidence-grounded.
- Prefer outcome language over implementation trivia.
- Default to three or four sections; never exceed five.
- Give proportionally more space to dominant changes.
- Keep the final prompt short, energetic, and readable.
