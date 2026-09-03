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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.config_models import DatabaseBackend
from basic_memory.models import Project
from basic_memory.runtime.jobs import RuntimeObservedIndexFile
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
        # An entity can hold a ready embedding the current pass no longer counts
        # as embeddable (its file became non-markdown), so clamp rather than
        # report negative outstanding work.
        embeddings_pending = max(0, embeddable - embedded)
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
        """Return (embeddable entities, entities with a ready embedding).

        With semantic search off there is no embedding work to wait for, so the
        stage reports zero of zero and settles immediately rather than parking
        the whole project in PENDING forever.
        """
        if not self.app_config.semantic_search_enabled:
            return 0, 0

        embeddable_result = await session.execute(
            text(
                "SELECT COUNT(*) FROM entity "
                "WHERE project_id = :project_id AND content_type = 'text/markdown'"
            ),
            {"project_id": project_id},
        )
        embeddable = int(embeddable_result.scalar() or 0)

        # Trigger: semantic search is enabled but the vector manifest table is
        # absent (a database created before the vector migrations, or a build
        # without the sqlite-vec extension).
        # Why: querying a missing table raises, which would take down the one
        # call a waiter polls.
        # Outcome: report nothing embedded, which is true -- the stage stays
        # PENDING and `bm reindex --embeddings` is the documented remedy.
        if not await self._vector_manifest_exists(session):
            return embeddable, 0

        embedded_result = await session.execute(
            text(
                "SELECT COUNT(DISTINCT entity_id) FROM search_vector_chunks "
                "WHERE project_id = :project_id AND embedding_status = 'ready'"
            ),
            {"project_id": project_id},
        )
        return embeddable, int(embedded_result.scalar() or 0)

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
