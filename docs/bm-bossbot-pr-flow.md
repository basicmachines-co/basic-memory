# BM Bossbot PR Flow

BM Bossbot is the required merge gate for Basic Memory pull requests. It reviews
the latest pull request head SHA after the regular test workflow succeeds, then
sets the `BM Bossbot Approval` commit status for that exact SHA.

PR images are generated on demand only (see `scripts/generate_pr_infographic.py`);
the automated per-PR image job was removed because it consumed API tokens on
every Bossbot run. Images are never part of merge eligibility.

## Quick Start

1. Work from a feature branch, not `main`.
2. Run the local verification that matches the change.
3. Commit with sign-off:

   ```bash
   git commit -s -m "docs(ci): explain bm bossbot pr flow"
   ```

4. Push the branch and open a PR with a semantic title.
5. Wait for the `Tests` workflow to pass.
6. Wait for the `BM Bossbot Approval` status to turn green for the current head
   SHA.
7. Merge only after normal CI and BM Bossbot are both green.

If a new commit is pushed, the old BM Bossbot approval no longer counts. Wait
for Bossbot to review the new head SHA.

## Using The PR Skill

Codex can run the repo-local PR skill from a feature branch:

```text
$pr-create
$pr-create "Italian movie poster"
$pr-create "80's action movies"
```

Use the plain form when you only want the PR workflow. Pass a theme when you
want an on-demand PR image to use a particular visual direction.

The skill:

- checks branch state, GitHub auth, commit sign-offs, and PR title shape,
- pushes the branch,
- creates or reuses the PR,
- adds an optional image theme block when a theme was supplied,
- watches the `BM Bossbot Approval` status,
- never merges and never enables auto-merge.

The optional theme block is managed in the PR body:

```markdown
<!-- BM_INFOGRAPHIC_THEME:start -->
Italian movie poster
<!-- BM_INFOGRAPHIC_THEME:end -->
```

The theme is creative direction only. It does not affect tests, review, merge
eligibility, or the required status check.

The image depicts the content of the pull request — its title, description,
and change shape (labels, linked issues, commit subjects, changed files) —
never the review outcome. Approval stamps, verdicts, and badges are
deliberately excluded from the imagery.

## What BM Bossbot Does

BM Bossbot runs from trusted repository code on `main`. It does not checkout or
execute untrusted PR head code. Instead, it collects PR metadata and diff context
through the GitHub API, gives that sanitized context to Codex, and validates the
structured review output deterministically.

Bossbot can approve only when all of these are true:

- the `Tests` workflow succeeded for the current PR head SHA,
- the PR is not a draft,
- the PR author is an owner, member, or collaborator,
- Codex returned valid review JSON,
- `reviewed_head_sha` matches the current PR head SHA,
- `review_complete` is true,
- `verdict` is `approve`,
- there are no blocking findings,
- every review thread on the PR is resolved — open inline comments (from
  Codex or humans) block approval until they are addressed and resolved.

The required status context is:

```text
BM Bossbot Approval
```

## When Bossbot Runs

The automatic workflow starts after the `Tests` workflow completes successfully
for a PR. This saves review tokens when normal CI is already failing.

You can also rerun it manually from GitHub Actions:

1. Open the `BM Bossbot` workflow.
2. Choose `Run workflow`.
3. Enter the PR number.

Manual runs still require a successful `Tests` workflow for the current head
SHA.

## Review Threads Re-Gate The Approval

Review activity re-evaluates the approval without re-running the full review:

- a new review, inline comment, or unresolved thread flips
  `BM Bossbot Approval` to failure for the current head SHA,
- resolving the last open thread restores a previously earned approval for
  that same head SHA,
- thread resolution alone can never upgrade a review that did not approve.

This means a PR cannot merge while reviewer feedback is sitting unaddressed,
even if the approval was green when the feedback arrived.

## PR Body Blocks

Bossbot writes a managed review summary into the PR body:

```markdown
<!-- BM_BOSSBOT_SUMMARY:start -->
...
<!-- BM_BOSSBOT_SUMMARY:end -->
```

If an image was generated on demand, it is published with provenance:

```markdown
<!-- pr-infographic:start -->
![BM Bossbot image for PR #123](...)
<!-- pr-infographic:end -->

<!-- BM_INFOGRAPHIC_PROVENANCE:start -->
...
<!-- BM_INFOGRAPHIC_PROVENANCE:end -->
```

The image provenance records choices like image mode, theme source, selected
visual direction, model, size, and quality. It intentionally does not dump the
full image prompt into the PR description.

Images and their provenance never affect `BM Bossbot Approval`.

## Fixing A PR After Bossbot Runs

If Bossbot requests changes:

1. Read the blocking findings in the PR body.
2. Fix the issue locally.
3. Run targeted verification.
4. Commit with sign-off and push.
5. Reply to and resolve the review threads you addressed.
6. Wait for `Tests` to pass.
7. Wait for Bossbot to approve the new head SHA.

Codex can use the companion skill:

```text
$fix-pr-issues
```

That flow collects review findings, failed checks, and PR comments, applies
fixes, verifies them, pushes the branch, and waits for the new Bossbot review.

## Troubleshooting

- `BM Bossbot Approval` is expected but not reported yet: the `Tests` workflow
  may still be running or may have failed.
- Bossbot skipped the PR: check whether the PR is a draft, whether tests passed
  for the current head SHA, and whether the author is an owner, member, or
  collaborator.
- No PR image: expected — images are on-demand only and never block merge.
- The PR changed after approval: push invalidates the old approval. Wait for the
  new head SHA to be reviewed.
- A manual Bossbot run will not replace failed tests. It only runs after a
  successful `Tests` workflow exists for the current head SHA.
- Approval flipped to failure after a review comment: address the feedback,
  then resolve the threads — the approval for the same head SHA is restored
  automatically once no unresolved threads remain.
