# Shared Local/Cloud Runtime Package Snapshot

This snapshot condenses current imports and responsibilities from the paired Basic Memory
repositories. The packages are released separately, and the cloud repository pins a Basic Memory
revision during coordinated changes.

## Core callers

```python
# basic_memory/deps/services.py and basic_memory/mcp/server.py
from basic_memory.cloud.note_content_writes import NoteContentMutationService
from basic_memory.cloud.note_content_materialization import drain_pending_materializations

# basic_memory/repository/accepted_note_search_repository.py
from basic_memory.indexing.accepted_note_search import AcceptedNoteSearchRow
from basic_memory.indexing.project_index_maintenance import delete_project_index_vector_rows
```

`basic_memory.cloud` contains portable note mutation, read, materialization, project-delete, and
directory-delete facades. Local API, CLI, and MCP entrypoints use them directly. The facades accept
storage, queue, repository, and scheduler capabilities supplied by the composition root; they do
not require a hosted runtime.

The accepted-note search repository owns explicit-session SQL for hot search rows, but its row
value and semantic-vector deletion helper live in `basic_memory.indexing`, which also contains
workflow orchestration and batch indexing.

## Cloud callers

```python
# basic_memory_cloud/api/deps/note_content_gateway.py
from basic_memory.cloud import NoteContentMutationService, NoteContentQueryService
from basic_memory.index.local_notes import LocalAcceptedNoteRepositories

# basic_memory_cloud/services/index/project_indexing.py
from basic_memory.index.local_project import LocalProjectIndexObservation, ProjectIndexRouteRequest

# basic_memory_cloud/api/deps/task_schedulers.py
from basic_memory.index.local_schedulers import LocalTaskScheduler
```

`LocalAcceptedNoteRepositories` creates project-scoped core repository objects that use the
caller's `AsyncSession`. It contains no filesystem behavior and is used unchanged by both local and
cloud composition roots.

The symbols imported from `local_project` and `local_schedulers` include runtime-neutral request,
observation, and scheduling contracts alongside actual filesystem-backed implementations.

## Current package roles

- `basic_memory.runtime`: storage-neutral identifiers, payloads, cleanup plans, and job values.
- `basic_memory.index`: local composition plus project-index route contracts and schedulers.
- `basic_memory.indexing`: accepted-note orchestration, materialization workflows, batch indexing,
  and some persistence values/helpers.
- `basic_memory.repository`: explicit-session data access, with a few upward imports from
  `indexing`.
- `basic_memory.cloud`: portable service facades used by both local and hosted entrypoints.

Behavior is stable and must remain so. A single package-tree rewrite would require a risky paired
release; small moves can use temporary re-exports while the cloud pin advances.
