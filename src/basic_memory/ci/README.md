# Basic Memory CI

Basic Memory CI turns meaningful GitHub delivery moments into durable
`project_update` notes in Basic Memory.

GitHub records the mechanics: pull requests, workflow runs, SHAs, URLs, labels,
and timestamps. The agent reads those facts and writes a short synthesis of what
changed and why it matters. The Basic Memory CLI owns authentication, schema
guidance, idempotency, and publishing.

The product voice is:

> GitHub records the mechanics. Basic Memory remembers what changed and why.

## Flow

```text
GitHub event
-> workflow eligibility filter
-> bm ci collect
-> Codex Action reads ProjectUpdateContext + memory-ci-capture prompt
-> Codex writes AgentSynthesis JSON
-> bm ci publish
-> Basic Memory project_update note
```

The v0 workflow is GitHub-only and uses `openai/codex-action@v1` as the
synthesis runner. The Codex step runs in read-only mode and does not receive the
Basic Memory API key.

## Setup CI/CD

Use `bm ci setup` from the GitHub repository root. The command installs the
workflow/config/prompt files and seeds the Basic Memory schema notes.

For the common cloud path:

```bash
bm cloud api-key save bmc_...
bm cloud workspace list
bm ci setup --project <project-name> --workspace <workspace-slug-or-id> --cloud --yes
```

Use the `Slug` column from `bm cloud workspace list` for `--workspace`; the
`Workspace ID` column also works when a slug is unavailable or ambiguous.

Prefer `--project-id <external_id>` when the same project name exists in more
than one workspace:

```bash
bm ci setup --project <project-name> --project-id <project-external-id> --cloud --yes
```

Then review and commit the generated files:

```text
.github/workflows/basic-memory.yml
.github/basic-memory/config.yml
.github/basic-memory/memory-ci-capture.md
```

Add these GitHub repository secrets:

- `OPENAI_API_KEY`: used only by `openai/codex-action`.
- `BASIC_MEMORY_API_KEY`: mapped to `BASIC_MEMORY_CLOUD_API_KEY` only for
  `bm ci publish`.

Add this optional GitHub repository variable only when using a non-default cloud
host:

- `BASIC_MEMORY_CLOUD_HOST`

Configure production deploy capture in `.github/basic-memory/config.yml`:

```yaml
project: <project-name>
workspace: <workspace-slug-or-id>
deploy_workflows:
  - Deploy Production
production_environments:
  - production
```

The generated workflow also uses `deploy_workflows` to populate
`on.workflow_run.workflows`. If you change deploy workflow names later, rerun
`bm ci setup --force` or update both `.github/basic-memory/config.yml` and
`.github/workflows/basic-memory.yml` together.

The generated workflow installs `basic-memory` from PyPI. When dogfooding an
unreleased `bm ci` change, temporarily edit the workflow install step to install
the branch or commit that contains the CI commands.

This repository's live workflow may temporarily install from the checked-out
repository while `bm ci` is being dogfooded ahead of the next package release.

After the files and secrets are in place, verify the first run by merging a test
PR or completing a configured production deploy workflow. The Auto BM workflow
should create or update a `project_update` note under:

```text
project-updates/github/<owner>/<repo>/
```

Failures in the Auto BM workflow fail only the project-update capture workflow;
they do not roll back the original merge or deploy.

## Commands

`bm ci setup`

Installs the repository automation files:

- `.github/workflows/basic-memory.yml`
- `.github/basic-memory/config.yml`
- `.github/basic-memory/memory-ci-capture.md`

It also seeds the canonical Basic Memory schema notes when they do not already
exist:

- `ProjectUpdate`
- `GitHubPullRequestUpdate`
- `GitHubProductionDeployUpdate`

`bm ci collect`

Reads the current GitHub event payload and normalizes it into
`ProjectUpdateContext`. This command decides whether the event is eligible.
Merged pull requests and configured successful production deploy workflow runs
are eligible. Routine CI runs, failed deploys, and unmerged PR closures are
no-ops. In v0, collection is intentionally limited to the GitHub event payload;
GitHub API enrichment for file lists, checks, reviews, or commit lists can be
added later without changing the publishing boundary.

`bm ci agent-schema`

Writes the optional `AgentSynthesis` JSON schema used by the generated workflow
as a CI guardrail. This schema is not a Basic Memory domain schema and is not
committed by setup.

`bm ci publish`

Combines deterministic GitHub facts with the agent synthesis and upserts a
Basic Memory `project_update` note. Agent-supplied identity fields are ignored;
source identity comes from `ProjectUpdateContext`.

## Auth Boundary

The generated workflow needs these secrets:

- `OPENAI_API_KEY`
- `BASIC_MEMORY_API_KEY`

`OPENAI_API_KEY` is passed only to the Codex Action. `BASIC_MEMORY_API_KEY` is
mapped to `BASIC_MEMORY_CLOUD_API_KEY` only for the publish step, where the
Basic Memory client uses it as `cloud_api_key`. The publish step also passes
`--cloud` so CI writes to the configured cloud project.

When `.github/basic-memory/config.yml` includes `workspace` and no `project_id`,
CI routes the project as `<workspace>/<project>`. Use a workspace slug there, or
prefer `project_id` when project names collide across workspaces.

## Idempotency

Project updates are upserted by stable GitHub identity:

- PR merge:
  `github:<owner>/<repo>:pull_request_merged:<pr_number>`
- Production deploy:
  `github:<owner>/<repo>:production_deploy_succeeded:<environment>:<workflow_run_id>`

The default note folder is:

```text
project-updates/github/<owner>/<repo>/
```

## Module Responsibilities

`project_updates.py` contains the v0 domain model and rendering helpers:

- `ProjectUpdateConfig`: non-secret repo configuration.
- `ProjectUpdateContext`: normalized immutable GitHub facts.
- `AgentSynthesis`: agent-authored summary fields.
- `ProjectUpdateNote`: final Basic Memory note payload.
- workflow, prompt, and schema-note seed rendering.

The CLI command group lives in `basic_memory.cli.commands.ci` and performs file
installation, event collection, schema seeding, and publish orchestration.
