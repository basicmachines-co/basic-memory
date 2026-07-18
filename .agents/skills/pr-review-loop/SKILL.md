---
name: pr-review-loop
description: Enforce the Basic Machines GitHub PR review loop before merging. Use whenever Codex is preparing to merge, squash-merge, auto-merge, declare a PR ready, monitor Codex comments, address review feedback, or wait for Codex approval on a GitHub PR, especially when the user says "approved", "merge", "ship", "PR is ready", "monitor Codex comments", or "address Codex feedback".
---

# PR Review Loop

## Hard Rule

Do not merge a PR merely because CI is green, the branch is mergeable, review threads are outdated, or no current Codex thread is visible.

Merge only when one of these is true:

- Codex has finished reviewing the latest head and left an explicit thumbs-up approval signal.
- The user explicitly overrides this gate with language like "merge without waiting for Codex approval" or "override Codex gate".

Codex often leaves that thumbs-up as a reaction on the PR description/body itself
(the GitHub Issue/PR object), not on its "Codex Review" issue comment. Do not
only inspect issue comments.

If Codex shows an eyes reaction on the PR body or a comment, it is reviewing.
Wait and keep checking.

If Codex leaves a comment, review comment, or inline thread, the PR is not approved. Use judgement: fix the code when the comment is right; reply with evidence when the comment is wrong or intentionally not worth changing.

## Signals

- Eyes reaction on the PR body or a comment: Codex is looking at the PR. This is pending, not approval.
- Thumbs-up reaction by `chatgpt-codex-connector[bot]` on the PR body/description: Codex approves/no suggestions. This is the common approval signal.
- Thumbs-up reaction by `chatgpt-codex-connector[bot]` on a Codex issue comment: also an approval signal, but this is not the only place to look.
- Codex issue comment saying "Didn't find any major issues": approval-like context, but confirm the PR body/comment thumbs-up or get an explicit user override.
- Codex comment or review thread: Codex found feedback. Treat it as blocking until addressed, replied to with a clear rationale, or explicitly overridden by the user.
- Outdated Codex comments: useful history, but not approval.
- Empty `reviewDecision`, `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`, and green checks: necessary context, but not Codex approval.

## Loop Workflow

1. Resolve the PR and current head SHA.

```bash
gh pr view <number> --json number,url,headRefOid,headRefName,mergeable,mergeStateStatus,statusCheckRollup
```

2. Read Codex state on the latest head. Check every GitHub surface where Codex
can leave state:

- PR body/description reactions: the approval thumbs-up may be here.
- PR issue comments: Codex posts "Codex Review" summaries here, including the reviewed commit.
- PR reviews and inline review comments: Codex posts actionable findings here.
- Review threads: unresolved current-head threads remain blocking even when older threads are outdated.

Any code push after a prior Codex approval invalidates that approval. A material
PR-body edit should restart the loop for the description, but it does not
invalidate the code-head review unless it changes the scope being reviewed.

Check the PR body reactions first, and verify the reacting actor:

```bash
gh api "repos/<owner>/<repo>/issues/<number>/reactions" \
  -H "Accept: application/vnd.github+json" \
  | jq '[.[] | select(.user.login == "chatgpt-codex-connector[bot]")
    | {content, created_at, user: .user.login}]'
```

`gh pr view --json reactionGroups` is useful for counts, but it does not show
which user reacted. Use the REST reactions endpoint above to prove Codex left
the thumbs-up on the PR body.

Then check Codex issue comments and confirm the latest "Reviewed commit" matches
the current head prefix:

```bash
gh api "repos/<owner>/<repo>/issues/<number>/comments" --paginate \
  | jq '[.[] | select(.user.login | test("chatgpt-codex-connector"))
    | {created_at, html_url, body: .body[0:240], reactions: .reactions}]'
```

Finally, check inline review comments on the current head:

```bash
head_sha="$(gh pr view <number> --json headRefOid --jq .headRefOid)"
gh api "repos/<owner>/<repo>/pulls/<number>/comments" --paginate \
  | jq --arg head "$head_sha" '
    [.[] | select((.user.login | test("chatgpt-codex-connector"))
      and (.commit_id == $head or .original_commit_id == $head))
      | {path, line, body, html_url}]'
```

If REST inline comments look current but you have replies on them, use GraphQL
review threads to distinguish unresolved blockers from addressed history.

3. If Codex has eyes and no thumbs-up, keep monitoring. Do not infer approval from silence.

4. If Codex leaves feedback, start addressing it immediately. Do not wait for all tests to complete before reading and acting on comments; that wastes review-loop time. Tests can keep running in parallel while you inspect the feedback.

5. For each Codex comment, use engineering judgement.

- If the comment identifies a real issue, patch it, run focused validation, push, and restart the loop on the new head.
- If the comment is wrong, stale, intentionally out of scope, or not worth changing, reply on GitHub with a concise rationale and evidence. You are not forced to make a code change.
- If the tradeoff is unclear, explain the tradeoff to the user and ask before choosing.

6. After every push, restart from step 1. A new head requires a new Codex response.

7. The loop is complete only when all of these are true on the same latest head:

- Required tests/checks are passing.
- Codex has no unaddressed current-head comments.
- Codex has left the thumbs-up approval signal, or the user explicitly overrode the gate.

8. Report the gate before merging:

```text
Codex gate: approved | waiting | blocking | overridden
Head: <sha>
Tests: passing | pending | failing
Evidence: <thumbs-up reaction, blocking comment URL, reply URL, or explicit user override>
```

9. Only run `gh pr merge` when the gate is `approved` or `overridden` and tests are passing on that same head.

## Failure Mode This Prevents

PR `basicmachines-co/basic-memory-cloud#1366` was merged after CI went green and existing Codex threads were outdated, but before Codex had left its thumbs-up. Codex then posted a P2 review comment on the merged head. This skill exists to prevent that exact mistake.
