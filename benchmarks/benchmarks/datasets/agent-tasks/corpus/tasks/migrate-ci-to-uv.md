---
title: Migrate CI to uv
type: task
status: active
steps:
  - Inventory pip usages across the workflows
  - Rewrite the test workflow around uv sync BMEVAL-ci-step2-51aa
  - Delete the requirements lockfiles and verify deploy
current_step: 2
context: CI migration to uv; wheel cache is keyed on uv.lock BMEVAL-ci-ctx-90bf
started: '2026-08-25'
---

# Migrate CI to uv

## Observations

- [status] Step 1 complete; the workflow rewrite is in review

## Relations

- instance_of [[Task]]
- relates_to [[2026-08-27 Infra Notes]]
