# Architecture

How the Basic Memory plugin works, flow by flow. For the design rationale and
decision history, see [DESIGN.md](../DESIGN.md).

## The bridge

Claude Code has its own **working memory** (auto-memory: short, in-context notes
Claude keeps per project). Basic Memory is a **durable graph** (Markdown files,
semantic + structured search, portable across tools). They do different jobs. The
plugin is the connective tissue that keeps each informed by the other.

```mermaid
flowchart LR
    subgraph CC["Claude Code session"]
        WM["Working memory<br/>(auto-memory)<br/>fast · in-context · per-project"]
    end
    subgraph BM["Basic Memory"]
        G["Durable graph<br/>(Markdown + SQLite)<br/>searchable · typed · portable"]
    end

    WM -- "PreCompact: checkpoint<br/>before forgetting" --> G
    G -- "SessionStart: brief<br/>tasks + decisions" --> WM

    classDef mem fill:#1f2430,stroke:#6A9BCC,color:#e8eaed;
    class WM,G mem;
```

The plugin ships **four surfaces**, in three layers:

```mermaid
flowchart TB
    subgraph Ambient["Ambient (lifecycle hooks)"]
        SS["SessionStart hook<br/>→ brief from the graph"]
        PC["PreCompact hook<br/>→ checkpoint to the graph"]
    end
    subgraph Background["Background (system prompt)"]
        OS["output-style<br/>→ search-first / capture / cite reflexes"]
    end
    subgraph Deliberate["Deliberate (slash commands)"]
        SK["/basic-memory:bm-setup · bm-remember · bm-share · bm-status"]
    end

    Ambient --> MCP["Basic Memory MCP server"]
    Background --> MCP
    Deliberate --> MCP
    MCP --> Files["Markdown files<br/>(local and/or cloud)"]
```

Everything routes through the Basic Memory MCP server (and the `bm` CLI for the
hooks). The plugin itself holds no state — configuration lives in
`.claude/settings.json`, content lives in your Basic Memory projects.

## SessionStart — the brief

When a session begins, the hook puts the most relevant slice of the graph in front
of Claude *before the first prompt*, so the session starts oriented instead of cold.

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant H as session_start.py
    participant BM as Basic Memory (bm CLI)
    participant C as Claude

    CC->>H: SessionStart (cwd, source)
    H->>H: read .claude/settings.json<br/>(primaryProject, secondaryProjects, teamProjects)
    par primary
        H->>BM: search type=task status=active
        H->>BM: search type=decision status=open
    and each shared project (parallel)
        H->>BM: search type=decision status=open
    end
    BM-->>H: results (or timeout → skip)
    H-->>CC: brief (plain stdout, <10k chars)
    CC->>C: brief injected into context
    Note over C: starts on active tasks,<br/>open decisions, team context
```

Key properties:
- **Structured, not fuzzy.** Queries filter on `type`/`status` frontmatter, so recall
  is deterministic — exactly the active tasks and open decisions, not "things that
  look similar."
- **Parallel.** Primary and shared-project queries run concurrently; total wall-clock
  is ~one query, not the sum.
- **Best-effort.** No Basic Memory, no config, or a slow cloud read never blocks or
  errors the session — the worst case is a missing or partial brief.
- **First-run aware.** With no config it nudges toward `/basic-memory:bm-setup`.

## PreCompact — the checkpoint

Right before Claude Code compacts the context window (and the texture of the session
would be lost), the hook writes a durable checkpoint so the next session can resume.

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant H as pre_compact.py
    participant BM as Basic Memory

    CC->>H: PreCompact (transcript_path, cwd)
    H->>H: read settings → primaryProject
    alt no primaryProject configured
        H-->>CC: exit (never write un-opted-in)
    else configured
        H->>H: extract opening request + recent thread
        H->>BM: write_note type=session status=open<br/>→ primaryProject/sessions/
        BM-->>H: ok
    end
    Note over CC: compaction proceeds;<br/>checkpoint surfaces in the next<br/>SessionStart brief
```

The checkpoint is a schema-conforming `type: session` note, so the *next* session's
SessionStart query (`type=session`) finds it. Capture is extractive today; an
LLM-summarized version is the planned enrichment (PreCompact has a ~600s budget).

## Capture while you work

The opt-in output style turns three behaviors into reflexes during normal work — no
command needed:

```mermaid
flowchart LR
    Q["User asks a recall question"] --> S["search the graph first<br/>(structured filters)"] --> A["answer with permalinks"]
    D["User makes a material decision"] --> W["write a type:decision note inline"]
```

Because decisions are captured **typed**, they show up in the next session's brief
automatically — the read and write sides reinforce each other.

## Teams — read across, share deliberately

On Basic Memory Cloud, the plugin reads team context into your brief but never
auto-writes to a shared project. Publishing back is always a manual gesture.

```mermaid
flowchart TB
    subgraph You["Your session"]
        P["primaryProject<br/>(personal capture)"]
    end
    subgraph Team["Team workspace"]
        T1["team/main"]
        T2["team/notes"]
    end

    T1 -- "read-only<br/>(SessionStart)" --> P
    T2 -- "read-only<br/>(SessionStart)" --> P
    P -- "/basic-memory:bm-share<br/>(deliberate, confirmed)" --> T2

    note["Auto-capture (checkpoints, /remember)<br/>writes ONLY to primaryProject"]
```

Team refs are workspace-qualified (`team/notes`) or `external_id` UUIDs, because
project names collide across workspaces. Reads route over the user's OAuth session.

## Where things live

| Path | Role |
|------|------|
| `hooks/session_start.py`, `hooks/pre_compact.py` | the ambient bridge (read / write) |
| `hooks/hooks.json` | registers the hooks |
| `output-styles/basic-memory.md` | the capture reflexes |
| `skills/{bm-setup,bm-remember,bm-share,bm-status}/` | the deliberate slash commands |
| `schemas/{session,decision,task}.md` | picoschema seeds (copied into your project at setup) |
| `.claude/settings.json` → `basicMemory` | per-project configuration |
| your Basic Memory projects | all actual content |
