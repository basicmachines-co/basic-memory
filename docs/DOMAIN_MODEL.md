# Basic Memory Domain Model

This document defines Basic Memory's product language, ownership, identity, and source-of-truth
rules. [ARCHITECTURE.md](ARCHITECTURE.md) describes code layers and dependency direction;
[ENGINEERING_STYLE.md](ENGINEERING_STYLE.md) describes how to implement changes. This document
describes what the system's concepts mean and which invariants the code must preserve.

## Design Center

Basic Memory gives humans and agents one canonical representation for note knowledge: Markdown.
Other resources may be addressable, but structured note and graph semantics are derived from
Markdown without making the resulting projections a competing source of knowledge.

Use domain terms in names, types, APIs, tests, and documentation. Do not let a transport DTO,
database table, queue message, or UI state redefine the product concept it represents.

## Core Concepts

### Project

A project is the core knowledge and isolation boundary. Every entity, observation, relation, and
search record belongs to exactly one project.

- A local project maps to a configured directory.
- A hosted workspace or tenant may provide routing, authorization, and storage around a project,
  but `workspace`, `tenant`, and `project` are not interchangeable domain terms.
- Project selection must be explicit or resolved once at an entrypoint. Lower layers receive the
  resolved project context rather than rediscovering global state.

### Note

A note is the user-facing Markdown document. It combines optional YAML frontmatter, prose,
observations, and relations.

- A note is Markdown, not a database row or editor view.
- `EntityMarkdown` is the parsed boundary representation of a note.
- A Markdown note maps to one project-scoped entity. An entity is broader than a note because the
  resource model can also represent non-Markdown content.

Use `note` in user-facing APIs and flows that specifically operate on Markdown. Use `entity` when
code genuinely operates on the broader indexed resource model.

### Entity

An entity is the project-scoped indexed representation of one file or resource. It is the node to
which graph and search projections attach.

- `id` is an internal database identity.
- `external_id` is the stable external/API identity and must survive ordinary updates and moves.
- `file_path` is the current project-relative storage location. It is unique within a project and
  may change when the resource moves.
- `permalink` is the human/agent-facing semantic address for Markdown content. It is
  project-scoped, may be absent when permalinks are disabled or inapplicable, and changes only
  according to the configured move and permalink policies.
- `title` is mutable display metadata, not identity.
- `checksum`, timestamps, size, parsed metadata, and indexed text describe synchronized state;
  they are not independent knowledge sources.

### Observation

An observation is a categorized semantic statement owned by one source entity. It contains
content and may carry category, context, and tags.

- An observation belongs to the same project as its entity.
- Its durable representation is the observation syntax in the source note.
- The database row and search document are projections rebuilt when the note is parsed.
- An observation has no independent lifecycle outside its source entity.

### Relation

A relation is a directed semantic statement owned by its source entity.

- `from_id` identifies the source entity that contains the relation.
- `relation_type` names the meaning of the edge.
- `to_id` identifies the resolved target when it exists.
- `to_name` preserves the author's target text even while the relation is unresolved.
- Source and resolved target belong to the same project graph.
- The durable representation is the relation syntax in the source note. Resolution enriches that
  statement; it does not replace it.

Incoming graph navigation does not transfer ownership to the target. Re-indexing the source note
may recreate, resolve, or remove its outgoing relations.

### NoteContent

`NoteContent` is the operational record of accepted Markdown bytes and their file materialization
state for one note entity. It is not a second note or a separate product concept.

- `markdown_content`, `db_version`, and `db_checksum` identify the accepted content version.
- `file_version` and `file_checksum` identify the version materialized to storage.
- `file_write_status` records whether materialization is pending, writing, synchronized, failed,
  or blocked by an external change.
- A note entity has at most one current `NoteContent` record.

Use this model to coordinate DB-first acceptance, retries, conflict detection, and read repair.
Do not let materialization mechanics leak into ordinary note or entity APIs unless callers need to
reason about acceptance or synchronization status.

### Search And Graph Indexes

Full-text rows, vector embeddings, chunks, observation rows, and relation rows are derived
projections. They exist to retrieve and traverse knowledge efficiently.

- A projection may lag an accepted write during asynchronous work.
- A projection must be rebuildable or reconcilable from canonical Markdown state.
- Search results point back to entities and notes; they do not become independent documents.
- Deleting or rebuilding an index must not mutate canonical content.

## Source Of Truth And Authority

"Markdown is the source of truth" describes the product representation. Operational authority
depends on the write phase and runtime.

### Local File-First Flow

The Markdown file is the durable authority. Direct human edits, CLI writes, and local MCP/API
writes converge on the file. Entity, graph, and search state are reconciled from the bytes that
landed on disk.

### Accepted DB-First Or Cloud-Style Flow

The service derives the final Markdown once and records that exact accepted version in
`NoteContent`. Until materialization finishes, that version is the operational authority for what
the system accepted. A worker or local provider materializes the same bytes to storage, records
the resulting file version and checksum, and publishes the derived graph and search state.

The DB-first accept path does not create a proprietary document model: the accepted value is still
Markdown, and materialization produces the portable file representation.

### Project Registry Authority

Project discovery differs by runtime. Local flows reconcile configured projects with the local
database; hosted flows may receive project and workspace context from the database or control
plane. Resolve that authority at the composition root or project service. Do not make repositories
or leaf helpers guess which registry wins.

## Write And Reconciliation Flows

### Create Or Update

1. Resolve the project and validate the boundary request.
2. Derive final frontmatter, permalink behavior, path, and Markdown bytes without mutating the
   caller's input.
3. Parse the accepted Markdown once into its entity, observations, and relations.
4. Persist through the runtime's file-first or DB-first path.
5. Reconcile the entity and its owned graph projections.
6. Update search projections from the same accepted or materialized content.
7. Resolve pending relation targets after the target entities exist.

Do not independently rebuild Markdown in multiple layers. Preparation, persistence, response, and
indexing must agree on the accepted bytes.

### External File Change

1. Detect the changed project-relative path.
2. Read and parse the file that now owns the local truth.
3. Upsert the entity while preserving stable external identity.
4. Replace the source entity's observations and outgoing relations from the parsed note.
5. Refresh search projections and relation resolution.

### Move

A move changes `file_path`. It preserves `external_id`, updates storage atomically, and applies the
configured permalink policy. Code that handles a move must keep frontmatter, entity state,
materialization state, graph links, and search records coherent.

### Delete

A delete removes canonical content through the service that owns the storage boundary, then
removes or reconciles its derived entity, graph, materialization, and search state. A caller must
not delete only a projection and report that the note was deleted.

## Boundary Vocabulary

- Use **schema** or **request/response model** for Pydantic transport and validation types.
- Use **model** with a qualifier when ambiguity matters: domain value, persistence model, parsed
  Markdown model, or runtime payload.
- Use **repository** for database access, not business decisions or file writes.
- Use **service** for domain operations that coordinate repositories, files, and projections.
- Use **client** for typed communication across an API boundary.
- Use **materialization** for writing accepted Markdown to durable file storage.
- Use **indexing** for producing retrieval state from content.
- Use **synchronization** or **reconciliation** for bringing canonical content and projections
  back into agreement.

Avoid generic names such as `manager`, `handler`, `data`, `item`, or `record` when the domain term
is known.

## Domain Change Checklist

Before adding a model, abstraction, or workflow, answer:

1. Which domain concept owns this behavior or state?
2. What is the canonical representation at this point in the lifecycle?
3. Which identifier is stable, and which locations or labels may change?
4. Is this value a domain concept, a boundary schema, persistence state, or a derived projection?
5. Which project, workspace, or tenant boundary constrains it?
6. Can the code express the operation with functions and typed values before introducing another
   service, hierarchy, or registry?
7. Which test proves the invariant across file, database, graph, search, and API surfaces?

## Related Documentation

- [Architecture](ARCHITECTURE.md)
- [Engineering Style](ENGINEERING_STYLE.md)
- [DeepWiki generated overview](https://deepwiki.com/basicmachines-co/basic-memory) — use as a
  navigation aid; checked-in documentation and source code are authoritative.
