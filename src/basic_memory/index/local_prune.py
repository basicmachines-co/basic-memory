"""Remove index entries for files the project's ignore patterns now exclude (#1254).

The project-index scan never lists an ignored file, so change planning marks its
entity as deleted — and then the storage-presence verifier vetoes the delete,
because the file is still on disk. That guard exists so a stale scan snapshot
cannot destroy a concurrent write; the side effect is that an entry indexed
before a pattern was added lives forever. Pruning is the explicit counterpart,
like ``git rm --cached``: the ignore rule is the proof of absence, the files are
left alone, and the index entries (entity, relations, search rows, vectors) go.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from basic_memory import db
from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.index.local_dependencies import LocalIndexProjectDependencies
from basic_memory.indexing.project_index_maintenance import (
    RepositoryProjectIndexMaintenanceStore,
    RepositoryProjectIndexMovedEntitySearchRefresher,
    TrustPlannedProjectIndexDeleteVerifier,
    run_project_index_delete_batches,
)


def plan_prune(
    indexed_file_paths: Iterable[str],
    project_root: Path,
    ignore_patterns: set[str],
) -> tuple[str, ...]:
    """Return the indexed project-relative paths the ignore patterns exclude, sorted."""
    return tuple(
        sorted(
            file_path
            for file_path in indexed_file_paths
            if should_ignore_path(project_root / file_path, project_root, ignore_patterns)
        )
    )


async def list_ignored_indexed_paths(
    dependencies: LocalIndexProjectDependencies,
    *,
    ignore_patterns: set[str] | None = None,
) -> tuple[str, ...]:
    """Indexed paths the project's current ignore patterns exclude.

    Uses the same pattern source as the scan (``.bmignore`` plus the project's
    ``.gitignore``) so the preview matches what indexing skips.
    """
    project_root = dependencies.file_service.base_path
    patterns = (
        ignore_patterns if ignore_patterns is not None else load_gitignore_patterns(project_root)
    )
    async with db.scoped_session(dependencies.session_maker) as session:
        indexed = await dependencies.entity_repository.get_all_file_paths(session)
    return plan_prune(indexed, project_root, patterns)


@dataclass(frozen=True, slots=True)
class PruneResult:
    """What one prune run removed and repaired."""

    deleted_paths: tuple[str, ...]
    deleted_entities: int
    # Surviving sources whose relations pointed at pruned entities; their search
    # rows were refreshed so they no longer claim a resolved target.
    refreshed_entity_ids: frozenset[int]


async def prune_ignored_entities(
    dependencies: LocalIndexProjectDependencies,
    paths: Sequence[str],
    *,
    batch_size: int = 100,
) -> PruneResult:
    """Delete the index entries at ``paths`` and repair linking notes' search rows."""
    if not paths:
        return PruneResult(deleted_paths=(), deleted_entities=0, refreshed_entity_ids=frozenset())
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=dependencies.session_maker,
        project_id=dependencies.project_id,
        external_vector_cleaner=dependencies.external_vector_cleaner,
        # The ignore rule is the proof of absence here, not storage: the file is
        # still on disk by definition, so the scan's presence verifier would veto
        # every one of these paths.
        delete_path_verifier=TrustPlannedProjectIndexDeleteVerifier(),
    )
    run = await run_project_index_delete_batches(
        deleted_paths=paths,
        batch_size=batch_size,
        delete_store=store,
    )
    if run.relation_cleanup_entity_ids:
        refresher = RepositoryProjectIndexMovedEntitySearchRefresher(
            session_maker=dependencies.session_maker,
            entity_repository=dependencies.entity_repository,
            entity_indexer=dependencies.search_service,
        )
        await refresher.refresh_moved_entities(sorted(run.relation_cleanup_entity_ids))
    return PruneResult(
        deleted_paths=tuple(paths),
        deleted_entities=run.total_deleted_entities,
        refreshed_entity_ids=run.relation_cleanup_entity_ids,
    )
