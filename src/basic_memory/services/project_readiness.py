"""Compute the readiness contract for one project (#1414).

Readiness answers a question a pending-count alone cannot: may a caller trust
what a read returns? The durable `project.last_indexed_at` marker separates
"never indexed" from "indexed and idle"; the three stages below then say what
is still owed, so a waiter blocks on the stage it depends on instead of on a
single aggregate that settles too early.

Every count here is derived state and is read without locking, per the
consistency model in CLAUDE.md: the numbers may lag a concurrent index pass by
one poll, which is exactly what a waiter is polling to observe.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.config_models import DatabaseBackend
from basic_memory.models import Entity, Project
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
)
from basic_memory.repository.search_repository_base import CURRENT_VECTOR_MANIFEST_PREDICATE
from basic_memory.repository.semantic_vector_index_factory import (
    resolve_semantic_vector_index_name,
)
from basic_memory.runtime.jobs import RuntimeObservedIndexFile
from basic_memory.services.search_service import entity_embeddings_enabled
from basic_memory.schemas.project_readiness import (
    ProjectIndexPhase,
    ProjectIndexReadiness,
    ProjectIndexStage,
    ProjectIndexStageName,
    combine_index_phases,
)


def _phase_for(*, indexed: bool, pending: int) -> ProjectIndexPhase:
    """Map one stage's outstanding work to its phase.

    ``indexed`` is the project-wide fact that a pass completed at least once;
    it dominates, because before that a zero pending count means "never
    counted" rather than "nothing outstanding".
    """
    if not indexed:
        return ProjectIndexPhase.NEVER_INDEXED
    return ProjectIndexPhase.PENDING if pending > 0 else ProjectIndexPhase.IDLE


def file_stage_counts(
    observed_files: Iterable[RuntimeObservedIndexFile],
    indexed_checksums: dict[str, str | None],
) -> tuple[int, int]:
    """Return (total, pending) for the file-indexing stage.

    ``total`` spans the union of observed and indexed paths so a pending delete
    (indexed, no longer on disk) cannot push ``pending`` past ``total`` and make
    a progress bar read backwards. A file counts as pending when it has no
    indexed row, when its observed checksum differs from the indexed one, or
    when the observation could not read a checksum at all -- the observer
    carries unreadable files through with ``checksum=None`` rather than dropping
    them, and unknown is not the same as current.
    """
    observed_paths: set[str] = set()
    pending = 0
    for observed in observed_files:
        observed_paths.add(observed.path)
        if observed.path not in indexed_checksums:
            pending += 1
        elif observed.checksum is None or observed.checksum != indexed_checksums[observed.path]:
            pending += 1

    pending_deletes = len(set(indexed_checksums) - observed_paths)
    total = len(observed_paths | set(indexed_checksums))
    return total, pending + pending_deletes


@dataclass(frozen=True, slots=True)
class ProjectReadinessService:
    """Read the derived counts behind one project's readiness contract."""

    session_maker: async_sessionmaker[AsyncSession]
    app_config: BasicMemoryConfig

    async def readiness_for_project_id(
        self,
        project_id: int,
        observed_files: Sequence[RuntimeObservedIndexFile],
    ) -> ProjectIndexReadiness:
        """Build readiness for a project named by its internal id."""
        async with db.scoped_session(self.session_maker) as session:
            project = await session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project with ID {project_id} not found")
        return await self.readiness_for(project, observed_files)

    async def readiness_for(
        self,
        project: Project,
        observed_files: Sequence[RuntimeObservedIndexFile],
    ) -> ProjectIndexReadiness:
        """Build the readiness contract for ``project`` given a fresh observation.

        The observation is passed in rather than re-scanned: the status route
        has already walked the project directory, and a second walk would double
        the cost of the one call a waiter polls.
        """
        indexed = project.last_indexed_at is not None

        async with db.scoped_session(self.session_maker) as session:
            indexed_checksums = await self._indexed_checksums(session, project.id)
            relations_pending = await self._resolvable_unresolved_relations(session, project.id)
            total_relations = await self._total_relations(session, project.id)
            embeddable, embedded = await self._embedding_counts(session, project.id)

        files_total, files_pending = file_stage_counts(observed_files, indexed_checksums)
        # No clamp: `embedded` is counted within the owed set, so it can never
        # exceed it (see _embedding_counts).
        embeddings_pending = embeddable - embedded
        stages = (
            ProjectIndexStage(
                name=ProjectIndexStageName.FILES,
                phase=_phase_for(indexed=indexed, pending=files_pending),
                pending=files_pending,
                total=files_total,
            ),
            ProjectIndexStage(
                name=ProjectIndexStageName.RELATIONS,
                phase=_phase_for(indexed=indexed, pending=relations_pending),
                pending=relations_pending,
                total=total_relations,
            ),
            ProjectIndexStage(
                name=ProjectIndexStageName.EMBEDDINGS,
                phase=_phase_for(indexed=indexed, pending=embeddings_pending),
                pending=embeddings_pending,
                total=embeddable,
            ),
        )
        return ProjectIndexReadiness(
            phase=combine_index_phases(stage.phase for stage in stages),
            last_indexed_at=project.last_indexed_at,
            files_on_disk=len(observed_files),
            indexed_entities=len(indexed_checksums),
            stages=stages,
        )

    async def _indexed_checksums(
        self,
        session: AsyncSession,
        project_id: int,
    ) -> dict[str, str | None]:
        """Load every indexed file path and its checksum for the project.

        The observer loads the same rows for its own checksum reuse
        (``RepositoryLocalProjectIndexedFileStatSource``), so this repeats one
        projection query. Sharing them would mean widening
        ``ProjectIndexObservation``, a runtime-neutral contract the cloud
        runtime also implements, to carry indexed state -- not worth it for a
        second read of a two-column projection on a route whose cost is already
        dominated by the full directory walk that produced the observation.
        """
        result = await session.execute(
            text("SELECT file_path, checksum FROM entity WHERE project_id = :project_id"),
            {"project_id": project_id},
        )
        return {str(row[0]): row[1] for row in result.all()}

    async def _resolvable_unresolved_relations(
        self,
        session: AsyncSession,
        project_id: int,
    ) -> int:
        """Count forward references a resolution pass would still wire up.

        A wikilink whose target does not exist is deliberately excluded. No pass
        will ever resolve it, so counting it would leave every ordinary
        knowledge base permanently PENDING and make IDLE unreachable -- the
        vacuous-ready bug inverted. What remains is the state the #1414 report
        actually hit: a link to a note that *does* exist, written moments ago,
        whose resolution has not run yet.

        Targets are matched on title or permalink, the two forms a wikilink is
        authored in. The resolver's own matching is broader, so this is a lower
        bound on resolvable references; a link that only the fuzzy matcher would
        catch settles one pass later than this reports.
        """
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM relation r "
                "JOIN entity e ON r.from_id = e.id "
                "WHERE e.project_id = :project_id AND r.to_id IS NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM entity t WHERE t.project_id = :project_id "
                "  AND (t.title = r.to_name OR t.permalink = r.to_name)"
                ")"
            ),
            {"project_id": project_id},
        )
        return int(result.scalar() or 0)

    async def _total_relations(self, session: AsyncSession, project_id: int) -> int:
        """Count all relations the project declares, resolved or not."""
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id "
                "WHERE e.project_id = :project_id"
            ),
            {"project_id": project_id},
        )
        return int(result.scalar() or 0)

    async def _embedding_counts(
        self,
        session: AsyncSession,
        project_id: int,
    ) -> tuple[int, int]:
        """Return (entities owed an embedding, those whose embedding is usable).

        Both sides are defined by what semantic retrieval actually does, because
        a definition that merely resembles retrieval drifts from it in both
        directions at once (#1414 review):

        - Owed is ``entity_embeddings_enabled``, the shared opt-out policy in
          ``search_service`` that ``sync_entity_vectors_batch`` uses to clear and
          skip a note. Counting an ``embed: false`` note as owed would leave the
          stage PENDING forever, since no pass will ever embed it.
        - Usable is ``CURRENT_VECTOR_MANIFEST_PREDICATE``, the literal predicate
          vector hydration applies. Counting a chunk left behind by an
          embedding-model or vector-index change as done would report IDLE while
          retrieval returns nothing for that note.

        Reported as a set difference rather than a subtraction of counts: an
        entity can hold a usable vector and no longer be owed one (its note took
        ``embed: false``), and only sets get that right without a clamp.

        One thing this deliberately does not verify is that a ready manifest row
        still has its physical vector. The only portable way to check is a
        search repository, and building one loads the embedding model — a cost a
        polled status route must not pay. ``bm project info`` verifies it, via
        ``ProjectService.get_embedding_status``, and recommends a rebuild.

        With semantic search off there is no embedding work to wait for, so the
        stage reports zero of zero and settles immediately rather than parking
        the whole project in PENDING forever.
        """
        if not self.app_config.semantic_search_enabled:
            return 0, 0

        # The opt-out policy reads a JSON metadata field, so the rows are loaded
        # and filtered in Python. Restating it as SQL would be a second copy of a
        # rule that already has an owner in search_service.
        result = await session.execute(
            select(Entity).where(
                Entity.project_id == project_id,
                Entity.content_type == "text/markdown",
            )
        )
        owed_entity_ids = {
            entity.id for entity in result.scalars().all() if entity_embeddings_enabled(entity)
        }
        if not owed_entity_ids:
            return 0, 0

        # Trigger: semantic search is on but the vector manifest table is absent
        # (a database predating the vector migrations, or a build without
        # sqlite-vec).
        # Why: querying a missing table raises, taking down the one call a waiter
        # polls.
        # Outcome: nothing is usable, which is true — the stage stays PENDING and
        # `bm reindex --embeddings` is the documented remedy.
        if not await self._vector_manifest_exists(session):
            return len(owed_entity_ids), 0

        # The identity comes from config rather than from a live repository's
        # `_semantic_vector_index_name` / `_embedding_model_key()`: those are only
        # populated once an instance has ensured its vector tables and loaded a
        # provider, and building one here would load the embedding model. The
        # config forms are defined to produce the same strings, and
        # get_embedding_status compares the same way.
        # `vector_sync_deferred_at` excludes an entity whose later shards were
        # never scheduled. Its written chunks satisfy the manifest predicate, so
        # without this an entity one shard into a large note counts as fully
        # embedded -- the third time a count and the thing it measures disagreed
        # (#1440 review). The marker is written by the sharded sync itself, in
        # `record_entity_vector_deferrals`, so the two cannot drift.
        usable_result = await session.execute(
            text(
                "SELECT DISTINCT entity_id FROM search_vector_chunks "
                "WHERE " + CURRENT_VECTOR_MANIFEST_PREDICATE + " "
                # Applied as a subquery so the shared predicate is used verbatim
                # rather than rewritten to carry a table alias.
                "AND entity_id NOT IN ("
                "  SELECT id FROM entity WHERE project_id = :project_id "
                "  AND vector_sync_deferred_at IS NOT NULL"
                ")"
            ),
            {
                "project_id": project_id,
                "vector_index": resolve_semantic_vector_index_name(
                    self.app_config,
                    self.app_config.database_backend,
                ),
                "embedding_model": configured_embedding_provider_identity(self.app_config),
            },
        )
        usable_entity_ids = {int(entity_id) for entity_id in usable_result.scalars().all()}
        return len(owed_entity_ids), len(owed_entity_ids & usable_entity_ids)

    async def _vector_manifest_exists(self, session: AsyncSession) -> bool:
        """Report whether the vector chunk manifest table is present."""
        if self.app_config.database_backend == DatabaseBackend.POSTGRES:
            query = text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'search_vector_chunks'"
            )
        else:
            query = text(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'search_vector_chunks'"
            )
        result = await session.execute(query)
        return result.first() is not None
