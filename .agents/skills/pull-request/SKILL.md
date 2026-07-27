---
name: pull-request
description: Drive GitHub pull request work end to end. Use when Codex is asked to open, update, describe, push to, monitor, review, address comments on, declare ready, or merge a PR. Covers branch hygiene, PR descriptions with why/what/testing/risk, CI checks, Codex review-loop monitoring, comment handling, and the final merge gate.
---

# Pull Request

Use this skill for the whole PR lifecycle, not only the moment of opening or merging.

## Runtime Setup

For Basic Memory Python tooling, use the project environment first:

```bash
source .venv/bin/activate
```

After activation, run Python scripts as `python ...`. For one-off commands where activation is awkward, prefer `./.venv/bin/python ...` or the repo's `uv run ...` patterns. Do not fall back to system `python`, `python3`, or global packages just because a command is missing.

## Flow

1. Inspect the live artifact first.
   - Use `gh pr view`, `gh issue view`, linked review comments, CI status, and current branch state before deciding what to change.
   - If the user links a specific PR discussion, inspect that exact discussion before broad edits.

2. Keep branch scope clean.
   - Base product-fix PRs on the intended remote base.
   - Avoid mixing workflow/skill/doc commits into unrelated product branches.
   - In `basic-memory`, prefer local branches in the main workspace unless the user asks for a worktree.

3. Implement and validate.
   - Read files fully before editing.
   - Make the smallest behaviorally complete change.
   - Run focused tests for the changed surface.
   - Run the repo's required gate, such as `just fast-check` for source changes or `just package-check` for agent/package changes, before calling the branch ready.

4. Write or update the PR description.
   - A PR without a useful description is not done.
   - Use the existing `pr-description` skill when available.
   - Make the body explain the change to a reviewer who did not watch the chat.

5. Open or update the PR.
   - Include linked issues, review comments, or specs.
   - Include exact validation commands and outcomes.
   - Mark draft only when the PR is intentionally not ready for review.

6. Enter the review loop immediately.
   - Use `pr-review-loop` after opening, pushing, updating the PR body in a meaningful way, or when the user asks whether the PR is ready.
   - Do not treat PR creation as the end of the task when the user expects review follow-through.

## Scope Discipline

Codex feedback is adversarial input, not authority to redefine the pull request. Before changing
code for a review finding:

1. Restate the PR's `Why`, acceptance criteria, and behavior being protected.
2. Trace or reproduce the concrete failure against the current head.
3. Classify the finding:
   - **In-scope blocker**: any regression introduced by the branch, or a direct violation of the
     stated behavior, acceptance criteria, security boundary, data integrity, or a required
     check. Fix it in the current PR.
   - **Sidequest / gold-plating**: speculative hardening, a broader concurrency model, unrelated
     cleanup, a new abstraction, or an improvement that is not required for the stated outcome.
     Push back with evidence and keep it out of the branch.
   - **Fast follow**: a real and material concern that deserves work but is separable from the
     current outcome. Keep the current PR focused and track it independently.

Narrow PR wording never makes a branch-introduced regression a fast follow. Treat every
regression caused by the current branch as an in-scope blocker, even when the `Why` or acceptance
criteria omitted the affected behavior.

Do not accept a `P1`, `P2`, or other severity label at face value. Severity must follow from a
reproducible impact and the product contract. In particular, do not add locks, leases, retries,
migrations, or generalized frameworks merely to close every theoretical interleaving when the
documented behavior permits eventual consistency.

For out-of-scope feedback, reply on the review thread with the scope boundary and supporting
evidence. If the concern is independently critical or otherwise worth scheduling, open a
fast-follow issue when the user has already authorized issue creation; otherwise provide the
proposed issue title/body and ask. Link the PR and review comment, state the concrete impact, and
give the follow-up its own acceptance criteria. Do not mix the follow-up implementation into the
current product branch.

## PR Description Standard

Every PR body should include these ideas, using headings that fit the repo's style:

- `Why`: the problem, reviewer comment, issue, incident, user need, or spec requirement that makes the change necessary now.
- `What Changed`: the concrete behavior or files changed, in reviewer-friendly language.
- `Implementation Details`: important design choices, constraints, tradeoffs, data-flow changes, or why a simpler-looking alternative was avoided.
- `Testing`: exact commands run and whether they passed. If something relevant was not tested, say so.
- `Risks / Follow-ups`: remaining uncertainty, rollout concerns, known deferred work, or why there are none.

Avoid PR bodies that only restate commit messages. Prefer a short but complete explanation over a long changelog.

## Codex Review Loop

Apply `pr-review-loop` as part of normal PR work:

- After opening a ready PR, check Codex state and CI.
- If Codex shows eyes, keep monitoring; eyes is pending, not approval.
- If Codex leaves feedback, classify its scope immediately while tests continue when possible.
- If the feedback is correct and in scope, patch, run focused validation, push, and restart the
  loop on the new head.
- If the feedback is wrong, speculative, gold-plating, or out of scope, push back with evidence,
  resolve the thread after replying, and keep the loop moving.
- If a separate concern is critical, create or propose a fast-follow issue instead of expanding
  the current PR.
- The loop completes only when required checks pass and Codex has approved the latest head with a thumbs-up, unless the user explicitly overrides the gate.

Do not merge, declare merge-ready, or move on as though finished until the loop state is explicit:

```text
Codex gate: approved | waiting | blocking | overridden
Head: <sha>
Tests: passing | pending | failing
Evidence: <thumbs-up reaction, blocking comment URL, reply URL, or explicit user override>
```

## Merge Discipline

Before merging:

- Confirm latest head SHA.
- Confirm required checks are passing on that head.
- Confirm no current-head Codex comments remain unaddressed.
- Confirm Codex thumbs-up or explicit user override.
- Ask or wait for the user's merge instruction unless they already gave it.

Never merge from green CI alone.
