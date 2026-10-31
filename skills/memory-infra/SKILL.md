---
name: memory-infra
description: "Operational fact registry: which server hosts which project, produced artifacts, and an append-only change log. Read before deployments so agents share one answer."
---

# Memory Infra

A registry of operational facts — servers, deployed projects, produced artifacts, and what changed — kept as ordinary notes so every agent and every tool reading the same project sees the same answer.

## Project scope

If the project is unknown, discover it with `bm_project(command="list")`.
Pass a nonempty `project` on every note, folder, context and schema call, even
with one project or a qualified `memory://` URL. Use the chosen project's name,
`workspace/project`, or external ID; `<project>` below is a placeholder for it.
Recent activity also requires `project`. Search accepts `project` for one
project, `projects` for selected projects, or `all_projects=True` explicitly.
Discovery and bundled manuals need no project. Keep the same scope when following
returned paths, paginating, or recovering from a refusal.

## When to Use

- **Before ops work** — deployment, config change, service restart, migration: read the registry instead of asking the user to re-explain hosts and paths.
- **After ops work** — record what changed, on which host, and what else it affects.
- **Registering a target** — a new server or a new project is being deployed for the first time.
- **Parallel agents** — two agents (or a human and an agent) work on the same host or project and need a shared handoff board.

## Why a registry, not a search

Search answers *"what might be related"*. Ops needs *"what is true"*. A search hit can be an outdated note; a registry entry is the answer the user already confirmed.

Three rules follow from that:

1. **Unregistered target → stop and ask.** Never deploy to, restart, or delete on a host or path that is not in the registry.
2. **Reality beats the note.** If the registry disagrees with what you observe, trust the observation, update the note, and log the correction.
3. **Never infer.** Do not derive a host, directory, or port from a search result, a filename, or a previous task's leftovers.

## Entities

Four note types. Create each one the first time its subject appears; keep them short and factual. Observation categories carry the structured fields, so schema validation (below) can read them — `host` is stored as `- [host] value`, not as prose.

### Server — `infra/servers/<server-slug>.md`

```markdown
---
title: Server A
tags: [infra, server]
type: Server
---

# Server A

Primary host for the marketing site and the internal dashboard.

## Observations
- [host] 203.0.113.10
- [ssh_user] deploy
- [purpose] Marketing site and internal dashboard
- [os] Ubuntu 24.04, 4 vCPU / 8 GB
- [caveat] `/etc/nginx/sites-enabled/` is hand-edited, no config management
- [caveat] Firewall allows 22, 80, 443 only
- [status] active

## Relations
- hosts [[Project A]]
- hosts [[Project C]]
```

### DeployedProject — `infra/projects/<project-slug>.md`

```markdown
---
title: Project A
tags: [infra, project]
type: DeployedProject
---

# Project A

Customer-facing site.

## Observations
- [path] `/opt/app-a`
- [service] systemd unit `app-a`
- [config] `/opt/app-a/.env`, secrets stay out of git
- [health] `GET /healthz` returns 200 on a good release
- [decision] Blue/green is overkill at this traffic; restart in place
- [status] active

## Relations
- deployed_on [[Server A]]
- shares_nginx_with [[Project C]]
```

### Artifact — `infra/artifacts/<artifact-slug>.md`

Notes what a task produced so a later task reuses it instead of rebuilding it.

```markdown
---
title: deploy-app-a.sh
tags: [infra, artifact]
type: Artifact
---

# deploy-app-a.sh

## Observations
- [path] `./scripts/deploy-app-a.sh` in the app-a repository
- [origin] Written by the 2026-10-30 release task
- [usage] Run from the repo root; needs SSH access to Server A
- [behavior] Pull, install, restart — idempotent

## Relations
- targets [[Server A]]
- produces [[Project A]]
```

### ChangeLog — `infra/changelog.md`

One table, append-only. Newest rows at the bottom while the file is short; fold it once it grows (below).

```markdown
---
title: Infra Change Log
tags: [infra, changelog]
type: ChangeLog
---

# Infra Change Log

## Observations
- [rule] Append-only. Never rewrite or delete a row.

| Date | Who | What | Impact |
|---|---|---|---|
| 2026-10-30 | agent-a | Deployed Project A v1.4.2 to Server A | Project A, Project C (shared nginx) |
```

**Who** is the agent, task, or person; **Impact** names the other servers, projects, or tasks that need to re-check something. Use `-` when nothing else is affected.

## Workflows

### Brief before work

1. Find the targets: `bm_find` with `filters` on `type`, or read `infra/servers/<slug>` / `infra/projects/<slug>` directly.
2. Read the target note plus every note its relations point at.
3. Read the last few rows of `infra/changelog.md` for the target.
4. Print a short brief and proceed — or stop and ask if a target is missing.

```text
Target: Server A (deploy@203.0.113.10)
Hosts:  Project A -> /opt/app-a (systemd app-a)
        Project C -> /opt/app-c (systemd app-c)
Caveats: nginx sites hand-edited; do not touch /etc/nginx/sites-enabled
Recent:  2026-10-30 Project A redeployed to v1.4.2
```

### Record after work

1. `bm_edit` the affected note(s): the path, version, service name, or status that actually changed. Change facts, not prose.
2. Append one row to `infra/changelog.md`.
3. For every other note named in **Impact**, add an observation saying what to re-check (`[risk]` or `[todo]` works well).
4. If the task produced a reusable script, config, or dataset, write an `Artifact` note.

### Register a new target

Only when the user provides the facts — you do not guess an IP, a path, or a port. Write the `Server` note first, then the `DeployedProject` note with a `deployed_on` relation, then the first changelog row.

### Fold the log

When `infra/changelog.md` passes ~50 rows, move the oldest rows to `infra/changelog-archive.md`, leave a single summary row in the main log (`| - | - | Archived N entries, see [[Infra Change Log Archive]] | - |`), and keep the recent 50 in place. Old rows are moved, never deleted.

## Red lines

- Do not operate on unregistered hosts, paths, or services. Ask first.
- Do not delete or rewrite changelog rows.
- Do not store credentials, tokens, or private keys in notes — record *where* the secret lives, not the secret.
- Do not describe intent as fact. `[decision]` and `[todo]` are intent; `[fact]` is observed state.

## Worked example

Two agents and a shared host:

1. Agent A deploys Project A: reads `Server A`, sees the nginx caveat, appends `2026-10-30 | agent-a | Deployed Project A v1.4.2 | Project A, Project C (shared nginx)`.
2. Agent B later adds a vhost for Project C: reads the same `Server A` note, sees that v1.4.2 landed and that nginx is hand-edited, checks ports before editing, then appends its own row.
3. Neither agent re-asks the user for the IP, the SSH user, or the deploy layout — both are in the registry, and both see the other's change.

## Schema

Optional but useful — validate the types so drift is caught. The schema notes live at `schema/Server.md`, `schema/DeployedProject.md`, `schema/Artifact.md`, `schema/ChangeLog.md`; see **memory-schema** for definitions, validation, and inference.

Schema fields are matched against observation categories, so each field name below must also appear as `- [field] value` in the note.

```markdown
---
title: Server
type: schema
entity: Server
version: 1
schema:
  host: string, address used for SSH or the API
  ssh_user?: string
  purpose?: string
  caveat?(array): string, things a deployer must not break
  status?(enum, current state): [active, maintenance, retired]
settings:
  validation: warn
---

# Server
```

## Relation to other skills

- **memory-tasks** tracks work in progress; **memory-infra** records the environment that work happens in.
- **memory-notes** is the general note format; this skill is its ops specialization.
- **memory-schema** validates the four types above.
- **memory-lifecycle** applies to project status (active → maintenance → retired); retiring a `Server` or `DeployedProject` means a status change plus a changelog row, not a delete.
