---
name: pr-create
description: Use when explicitly creating or updating a Basic Memory pull request. This compatibility entry point delegates the full workflow to pull-request and the latest-head Codex gate to pr-review-loop.
---

# Create A Basic Memory PR

Create or update a pull request by applying `pull-request` in full. Use
`pr-description` for the body and enter `pr-review-loop` as soon as the PR is
open or materially updated. This skill never merges a PR.

## Compatibility

`$pr-create` remains available for existing prompts, but its former BM Bossbot
and automatic PR-infographic workflow has been retired. Do not wait for the
deleted `BM Bossbot Approval` status or `.github/workflows/bm-bossbot.yml`.

If a legacy `$pr-create "<theme>"` prompt includes an image theme, do not add
the retired managed theme or provenance blocks. Explain that the automatic PR
image path is no longer part of this workflow.

## Workflow

1. Read and follow `pull-request` for branch hygiene, validation, pushing, and
   PR creation or reuse.
2. Read and follow `pr-description` when creating or materially updating the
   PR body.
3. Confirm commits are signed off, the title follows the repository's semantic
   PR-title format, and the validation appropriate to the changed surface has
   passed.
4. Push the branch and create or reuse the PR. Do not enable auto-merge.
5. Read and follow `pr-review-loop` on the latest head SHA. Green CI alone is
   not approval.
6. If Codex or CI reports a blocker, use `fix-pr-issues`, push the fix, and
   restart `pr-review-loop` for the new head.

## Report

Return the PR URL, current head SHA, validation run, check status, and Codex
gate evidence. Report pending or blocking state explicitly. Do not merge unless
the user separately requests it and the current-head gate permits it.
