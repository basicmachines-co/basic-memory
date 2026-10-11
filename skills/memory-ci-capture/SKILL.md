---
name: memory-ci-capture
description: "Internal CI skill: synthesize GitHub delivery context into a Basic Memory project update. Use after `bm ci collect`; return only AgentSynthesis JSON for `bm ci publish`."
metadata:
  # CI-only: hidden from `npx skills` (INSTALL_INTERNAL_SKILLS=1 shows it) and
  # left out of the release archives by scripts/build_skills_dist.py.
  internal: true
---

# Memory CI Capture

Turn a meaningful GitHub delivery moment into project memory. GitHub records the
mechanics. Basic Memory remembers what changed and why.

## Inputs

Read the `ProjectUpdateContext` JSON produced by `bm ci collect` at
`.github/basic-memory/project-update-context.json`. Treat it as the immutable
source of truth for repository, event type, PR number, workflow run, SHA, source
URL, timestamps, and deployment environment.

Do not invent tests, deploy checks, linked issues, product impact, or decisions.
If evidence is absent, say so briefly in `verification` or leave the field empty.

## Output

Return only JSON matching the `AgentSynthesis` shape:

```json
{
  "summary": "What changed.",
  "why_it_matters": "Why this update matters for future humans and agents.",
  "user_facing_changes": [],
  "internal_changes": [],
  "verification": [],
  "follow_ups": [],
  "decision_candidates": [],
  "task_candidates": []
}
```

## Synthesis Rules

- Prefer a short explanation over a commit-by-commit changelog.
- Preserve intent, changed behavior, source links, verification evidence present
  in the context, and concrete follow-ups.
- Put explicit product or architecture decisions in `decision_candidates` only
  when the source context clearly contains them.
- Put future work in `task_candidates` only when it is concrete enough to act on.
- Keep the tone factual and useful. This is project memory, not marketing copy.

## Event Guidance

For merged pull requests, focus on why the PR existed, what area changed, what
issues it advanced or closed, and what verification evidence appears in the
context.

For production deploys, focus on what reached production, the deployed SHA,
environment, workflow run, and verification evidence. Do not overclaim success
beyond the workflow and source facts.
