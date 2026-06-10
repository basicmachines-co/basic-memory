---
name: pr-create
description: Use when creating or updating a Basic Memory pull request from Codex with BM Bossbot merge-gate monitoring.
---

# Create A Basic Memory PR

Create or update a pull request for the current branch, then wait for BM
Bossbot to approve the latest head SHA. This skill never merges a PR.

## Inputs

- Optional `<theme>`: free-form visual direction for the non-gating PR
  image. Example: `$pr-create "Italian movie poster"`.
- Treat `<theme>` as style guidance only. It must not affect PR readiness,
  BM Bossbot review, status checks, or merge behavior.

## How To Use

Ask Codex to use the skill from a feature branch:

```text
$pr-create
$pr-create "Italian movie poster"
$pr-create "80's action movies"
```

Use the plain form when you only want the PR workflow. Pass a theme when you
want the non-gating image to lean toward a particular visual direction. The
theme can be specific ("Rembrandt-inspired approval scene") or broad ("let the
model choose from BM categories").

## What Happens

1. Codex checks the branch, local verification, GitHub auth, commit sign-offs,
   and semantic PR title shape.
2. Codex pushes the branch, creates or reuses the PR, and adds the optional
   `BM_INFOGRAPHIC_THEME` block when a theme was supplied.
3. BM Bossbot runs from trusted base code, reviews sanitized PR metadata and
   diff context, and sets the required `BM Bossbot Approval` status for the
   exact head SHA.
4. If approval succeeds, BM Bossbot may publish a non-gating image block and a
   provenance block:

   ```markdown
   <!-- BM_INFOGRAPHIC_PROVENANCE:start -->
   ...
   <!-- BM_INFOGRAPHIC_PROVENANCE:end -->
   ```

   The provenance records the image mode, theme source, selected visual
   direction, and image settings. It is for review/debugging context only.
5. Codex reports the PR URL, head SHA, checks watched, verification run, and BM
   Bossbot verdict.

The skill never merges, never enables auto-merge, and never treats the image or
provenance block as a gate. The only required merge signal is the
`BM Bossbot Approval` status on the current PR head SHA.

## Preflight

1. Confirm the repo and branch:
   - `git status --short --branch`
   - stop if detached or on `main`
   - keep unrelated user changes intact

2. Confirm GitHub access:
   - `gh auth status`
   - `gh repo view --json nameWithOwner,defaultBranchRef,url`

3. Check PR readiness:
   - commits are signed off with `git commit -s`
   - title uses the repo semantic format
   - local verification appropriate to the change has run

## Create Or Reuse

1. Push the branch:
   - `git push -u origin HEAD`

2. Check for an existing PR:
   - `gh pr view --json number,url,headRefOid,mergeStateStatus,statusCheckRollup`

3. If no PR exists, create one:
   - `gh pr create --fill`
   - adjust the title if it does not satisfy the semantic PR title workflow

4. If `<theme>` is provided, add or update this managed block in the PR body:

```markdown
<!-- BM_INFOGRAPHIC_THEME:start -->
<theme>
<!-- BM_INFOGRAPHIC_THEME:end -->
```

Keep the rest of the PR body intact. The theme is non-gating image guidance
only.

5. Do not merge. Do not enable auto-merge.

## Watch The Gate

1. Trigger or wait for `.github/workflows/bm-bossbot.yml`.
2. Watch the required commit status named `BM Bossbot Approval`.
3. Treat approval as valid only when it is green for the current `headRefOid`.
4. If the branch changes after approval, wait for BM Bossbot to review the new
   head SHA.
5. If BM Bossbot fails or requests changes, use `$fix-pr-issues`.

## Report

Return the PR URL, current head SHA, checks watched, verification run, and the
BM Bossbot verdict. Include the image `<theme>` if one was supplied. Be
explicit when any check is still pending.
