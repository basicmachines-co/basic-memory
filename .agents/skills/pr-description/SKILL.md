---
name: pr-description
description: Use when opening or updating a GitHub PR - writes a detailed PR description that explains what changed, why it changed, how it was implemented, and how it was tested.
license: MIT
---

# PR Description Writer

Create and publish high-quality PR descriptions that help reviewers understand intent, implementation details, and verification steps.

## When to Use

- Opening a new PR
- Refreshing a stale or minimal PR description
- Capturing implementation rationale for future debugging

## Goals

Every PR body should clearly answer:

1. What changed?
2. Why did we make this change now?
3. How was it implemented?
4. How was it tested?
5. What risks or follow-ups remain?

## Workflow

1. Discover PR context and changed files.
2. Extract intent from commit(s) and code changes.
3. Draft a structured PR body.
4. Apply the body to the PR.
5. Re-open the PR view and confirm body/labels.

## Commands

```bash
# Identify the active PR for current branch
gh pr view --json number,title,url,baseRefName,headRefName

# Inspect changed files and patch summary
gh pr diff --name-only
gh pr diff

# Review commit messages for intent
git log --oneline --decorate -n 10

# Apply updated body
gh pr edit <pr-number> --body-file /tmp/pr-body.md
```

## Body Structure

Use the template in [references/pr-body-template.md](references/pr-body-template.md).

Required sections:

- `## Why`
- `## What Changed`
- `## Implementation Details`
- `## Testing`
- `## Risks / Follow-ups`

## Quality Bar

- Be specific: name concrete files, routes, scripts, and commands.
- Explain tradeoffs and constraints, not just mechanics.
- Testing section must include exact commands run and outcomes.
- If something is not tested, state it explicitly.

## Optional Enhancements

- Add `## Screenshots` for UI work.
- Add `## Rollout Notes` when release sequencing matters.
- Add `## Reviewer Guide` for large diffs.
