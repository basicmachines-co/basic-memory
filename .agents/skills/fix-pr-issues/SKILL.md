---
name: fix-pr-issues
description: Use when addressing Basic Memory pull request feedback, failed checks, or Codex review blockers, then re-running the latest-head review gate.
---

# Fix Basic Memory PR Issues

Resolve PR feedback and failed checks, then apply `pr-review-loop` to the new
head SHA. This skill never merges a PR.

## Gather

1. Identify the live PR and latest head:
   - `gh pr view --json number,url,headRefOid,mergeStateStatus,statusCheckRollup`
2. Collect the exact feedback:
   - PR comments and review summaries
   - inline review comments and unresolved review threads
   - failed GitHub Actions jobs and relevant logs
   - Codex reactions and latest-head review state described by `pr-review-loop`
3. Build a short issue ledger with the source, concrete problem, expected fix,
   and verification needed for each item.

## Fix

1. Address one ledger item at a time.
2. Read each file in full before editing it.
3. Keep diffs narrow and preserve unrelated user changes.
4. Run the smallest meaningful verification first, then widen as needed.
5. Commit changed code or documentation with `git commit -s`.

If a comment is wrong, stale, intentionally out of scope, or not worth the
tradeoff, reply with concise evidence instead of forcing a code change.

## Push And Recheck

1. Push the branch.
2. Confirm checks are running against the new `headRefOid` and watch them to
   completion.
3. Apply `pr-review-loop` in full. A prior Codex approval is stale after a code
   push; require the current-head approval signal and zero unresolved,
   non-outdated Codex threads.
4. If new feedback appears, add it to the ledger and repeat the loop.

## Reply

For each addressed comment or blocker, reply with the fix commit, verification
run, and current Codex gate status. Resolve a substantive thread only after the
fix or rationale is posted with evidence.
