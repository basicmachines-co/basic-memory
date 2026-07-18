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

If Codex's newest fresh reaction on the PR body or a relevant comment is eyes,
it is reviewing. Wait and keep checking.

If Codex leaves a comment, review body, or inline thread containing substantive
feedback, the PR is not approved. Use judgement: fix the code when the comment
is right; reply with evidence when it is wrong or intentionally not worth
changing. A no-issue summary or boilerplate-only review body is not a blocker,
but it also does not replace the required thumbs-up.

## Signals

- Eyes reaction on the PR body or a comment: pending when it is newer than the
  latest fresh thumbs-up on that same surface.
- Thumbs-up reaction by `chatgpt-codex-connector[bot]` on the PR body/description: Codex approves/no suggestions. This is the common approval signal.
- Thumbs-up reaction by `chatgpt-codex-connector[bot]` on a Codex issue comment: also an approval signal, but this is not the only place to look.
- Codex issue comment saying "Didn't find any major issues": approval-like context, but confirm the PR body/comment thumbs-up or get an explicit user override.
- Latest current-head Codex review per actor with `CHANGES_REQUESTED` or a
  substantive review body: blocking feedback even when it has no inline thread.
- Codex comment, review body, or review thread containing substantive feedback:
  blocking until addressed, replied to with a clear rationale, or explicitly
  overridden by the user.
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
- Review threads: unresolved, non-outdated Codex threads remain blocking across pushes. Do not
  infer thread state from the placement commit recorded on individual comments.

Any code push after a prior Codex approval invalidates that approval. A material
PR-body edit should restart the loop for the description, but it does not
invalidate the code-head review unless it changes the scope being reviewed.

Check the PR body reactions first, and verify both the reacting actor and the
reaction's freshness for the current head and current PR description. The
status rollup is scoped to the current head SHA, while GraphQL `lastEditedAt`
captures later edits to the PR object. Use the later timestamp as the approval
lower bound:

```bash
head_state_json="$(
  gh pr view <number> --json headRefOid,statusCheckRollup
)" || exit 1
head_sha="$(printf '%s' "$head_state_json" | jq -r '.headRefOid')"
head_started_at="$(
  printf '%s' "$head_state_json" \
    | jq -r '[.statusCheckRollup[] | .startedAt // empty] | min // empty'
)"

edit_state_json="$(
  gh api graphql \
    -F owner=<owner> \
    -F name=<repo> \
    -F number=<number> \
    -f query='query($owner:String!,$name:String!,$number:Int!){
      repository(owner:$owner,name:$name){
        pullRequest(number:$number){headRefOid lastEditedAt}
      }
    }'
)" || exit 1
edit_head_sha="$(
  printf '%s' "$edit_state_json" \
    | jq -r '.data.repository.pullRequest.headRefOid'
)"
body_edited_at="$(
  printf '%s' "$edit_state_json" \
    | jq -r '.data.repository.pullRequest.lastEditedAt // empty'
)"

if [ "$edit_head_sha" != "$head_sha" ]; then
  echo "Head changed while checking review state; reaction is not approval."
  exit 1
fi

body_reactions_available=true
if [ -z "$head_started_at" ]; then
  body_reactions_available=false
  approval_not_before="$body_edited_at"
  echo "No timestamped current-head checks; skipping PR-body reactions."
else
  approval_not_before="$(
    jq -nr \
      --arg head_started_at "$head_started_at" \
      --arg body_edited_at "$body_edited_at" \
      '[$head_started_at, $body_edited_at]
      | map(select(length > 0))
      | max // empty'
  )"
fi

if [ "$body_reactions_available" = true ]; then
  reactions_json="$(
    gh api "repos/<owner>/<repo>/issues/<number>/reactions" --paginate --slurp \
      -H "Accept: application/vnd.github+json"
  )"

  echo "Fresh Codex reaction state:"
  printf '%s' "$reactions_json" \
    | jq --arg approval_not_before "$approval_not_before" '
      def latest_reaction($content):
        [.[][]
        | select(.user.login == "chatgpt-codex-connector[bot]"
          and .content == $content
          and .created_at >= $approval_not_before)
        | {content, created_at, user: .user.login}]
        | sort_by(.created_at)
        | last // null;

      {approval: latest_reaction("+1"), pending: latest_reaction("eyes")}
      | .state = (
          if .approval != null
            and (.pending == null
              or .approval.created_at > .pending.created_at)
          then "approved"
          elif .pending != null then "pending"
          else "none"
          end
        )'
fi
```

`gh pr view --json reactionGroups` is useful for counts, but it does not show
which user reacted. Use the REST reactions endpoint above to prove Codex left
the thumbs-up after both current-head activity began and the PR object was last
edited. A reaction state of `approved` satisfies the reaction portion of the
gate. `pending` means the newest fresh signal is eyes, so keep waiting; a newer
thumbs-up supersedes an older eyes reaction. If the current head has no
timestamped status/check activity, skip PR-body reactions and continue to the
review and issue-comment checks below instead of exiting the workflow.

Then check Codex issue comments and confirm the latest "Reviewed commit" matches
the current head prefix:

```bash
gh api "repos/<owner>/<repo>/issues/<number>/comments" --paginate \
  --slurp \
  | jq '[.[][] | select(.user.login | test("chatgpt-codex-connector"))
    | {id, created_at, html_url, body: .body[0:240]}]'
```

When PR-body reactions were skipped, only use an issue comment that names the
exact current head and, when `body_edited_at` is non-empty, was created after
that edit. Its thumbs-up must also pass the actor and `approval_not_before`
check below.

Top-level pull-request reviews are a separate API surface from issue comments
and review threads. Fetch every page and inspect Codex reviews submitted for the
exact current head:

```bash
head_sha="$(gh pr view <number> --json headRefOid --jq '.headRefOid')"

gh api "repos/<owner>/<repo>/pulls/<number>/reviews" --paginate --slurp \
  | jq --arg head_sha "$head_sha" \
    '[.[][]
    | select((.user.login | test("chatgpt-codex-connector"))
      and .commit_id == $head_sha)
    | {id, user: .user.login, state, submitted_at, html_url, body}]
    | sort_by(.user, .submitted_at, .id)
    | group_by(.user)
    | map(last)'
```

Evaluate only the latest current-head review returned for each Codex actor; a
newer review supersedes that actor's earlier top-level state on the same head.
A latest `CHANGES_REQUESTED` review is blocking. Read every latest non-empty
review body and address any substantive finding even when the review has no
inline thread. A boilerplate-only `COMMENTED` review that merely accompanies
inline findings is not an additional blocker after those findings are resolved;
it is also not an approval signal. Review-thread resolution remains a separate
gate below and is never superseded by top-level review history alone.

For a relevant Codex issue comment, verify any approval reaction by actor. The
aggregate reaction counts on the comment do not identify who reacted:

```bash
gh api "repos/<owner>/<repo>/issues/comments/<comment-id>/reactions" \
  --paginate --slurp \
  -H "Accept: application/vnd.github+json" \
  | jq --arg approval_not_before "$approval_not_before" '
    def latest_reaction($content):
      [.[][]
      | select(.user.login == "chatgpt-codex-connector[bot]"
        and .content == $content
        and .created_at >= $approval_not_before)
      | {content, created_at, user: .user.login}]
      | sort_by(.created_at)
      | last // null;

    {approval: latest_reaction("+1"), pending: latest_reaction("eyes")}
    | .state = (
        if .approval != null
          and (.pending == null
            or .approval.created_at > .pending.created_at)
        then "approved"
        elif .pending != null then "pending"
        else "none"
        end
      )'
```

Finally, query GraphQL review threads. GitHub records comment placement SHAs in
the REST payload, but only the thread exposes whether feedback remains unresolved
and non-outdated after a follow-up push:

```bash
gh api graphql --paginate --slurp \
  -F owner=<owner> \
  -F name=<repo> \
  -F number=<number> \
  -f query='query(
    $owner:String!
    $name:String!
    $number:Int!
    $endCursor:String
  ){
    repository(owner:$owner,name:$name){
      pullRequest(number:$number){
        reviewThreads(first:100,after:$endCursor){
          nodes{
            id isResolved isOutdated path line
            comments(first:100){
              nodes{author{login} body url createdAt commit{oid}}
            }
          }
          pageInfo{hasNextPage endCursor}
        }
      }
    }
  }' \
  | jq '[.[].data.repository.pullRequest.reviewThreads.nodes[]
    | select((.isResolved | not) and (.isOutdated | not))
    | select(any(.comments.nodes[];
        .author.login | test("chatgpt-codex-connector")))
    | {id, path, line, comments: .comments.nodes}]'
```

An empty result across every page means there are no unresolved, non-outdated
Codex threads. Keep outdated threads as review history, but do not treat their
comment SHAs as the current resolution state.

3. If the latest fresh reaction state is `pending`, keep monitoring. Do not
infer approval from silence.

4. If Codex leaves feedback, start addressing it immediately. Do not wait for all tests to complete before reading and acting on comments; that wastes review-loop time. Tests can keep running in parallel while you inspect the feedback.

5. For each Codex comment, use engineering judgement.

- If the comment identifies a real issue, patch it, run focused validation, push, and restart the loop on the new head.
- If the comment is wrong, stale, intentionally out of scope, or not worth changing, reply on GitHub with a concise rationale and evidence. You are not forced to make a code change.
- If the tradeoff is unclear, explain the tradeoff to the user and ask before choosing.

6. After every push, restart from step 1. A new head requires a new Codex response.

7. The loop is complete only when all of these are true on the same latest head:

- Required tests/checks are passing.
- Codex has no unaddressed current-head comments, top-level review findings, or
  unresolved non-outdated review threads.
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
