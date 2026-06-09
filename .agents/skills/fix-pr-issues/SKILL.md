---
name: fix-pr-issues
description: Use when addressing Basic Memory pull request feedback, failed checks, or BM Bossbot blockers from Codex.
---

# Fix Basic Memory PR Issues

Resolve PR feedback and failed checks, then wait for BM Bossbot to approve the
new head SHA. This skill never merges a PR.

## Gather

1. Identify the PR:
   - `gh pr view --json number,url,headRefOid,mergeStateStatus,statusCheckRollup`

2. Collect feedback:
   - PR comments and review summaries
   - inline review comments and unresolved review threads
   - failed GitHub Actions jobs and relevant logs
   - the managed `BM_BOSSBOT_SUMMARY` block in the PR body

3. Build a short issue ledger:
   - source
   - concrete problem
   - expected fix
   - verification needed

## Fix

1. Address one ledger item at a time.
2. Read each file in full before editing it.
3. Keep diffs narrow and preserve unrelated user changes.
4. Run the smallest meaningful verification first, then widen as needed.
5. Commit with `git commit -s` when code or docs changed.

## Push And Recheck

1. Push the branch.
2. Watch checks for the new `headRefOid`.
3. Wait for the required `BM Bossbot Approval` status to pass on that exact SHA.
4. If BM Bossbot reviews an older SHA, treat the approval as stale and keep
   waiting for the current one.

## Reply

For each addressed comment or blocker, reply with the fix commit, verification
run, and current BM Bossbot status. Do not resolve or dismiss substantive
feedback without evidence.
