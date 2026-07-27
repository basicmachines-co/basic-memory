"""Portable async file-index metadata checking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.file_index_planning import (
    FileIndexChecksum,
    FileIndexDecision,
    FileIndexDecisionStatus,
    FileIndexPath,
    FileIndexPlan,
    FileIndexTarget,
    build_file_index_plan,
    move_orphan_file_index_decision,
    plan_file_index_target_from_current,
    plan_file_index_target_from_observed,
    plan_legacy_file_index_targets,
)


class IndexedFileChecksumSource(Protocol):
    """Capability that reads accepted checksums from the indexing store."""

    async def load_indexed_file_checksums(
        self,
        file_paths: Sequence[FileIndexPath],
    ) -> Mapping[FileIndexPath, FileIndexChecksum | None]:
        """Return indexed checksums keyed by file path."""


class CurrentFileChecksumSource(Protocol):
    """Capability that reads current file checksums from storage metadata."""

    async def load_current_file_checksum(
        self,
        file_path: FileIndexPath,
    ) -> FileIndexChecksum | None:
        """Return the current checksum for one file, or None when it is missing."""


class IndexedFileChecksumRow(Protocol):
    """Tuple-like row containing file path and indexed checksum."""

    def __getitem__(self, index: int, /) -> object:
        """Return a row field by positional index."""


class IndexedFileChecksumRepository(Protocol):
    """Repository capability that loads accepted checksums for file paths."""

    async def get_by_file_paths(
        self,
        session: AsyncSession,
        file_paths: Sequence[FileIndexPath],
    ) -> Sequence[IndexedFileChecksumRow]:
        """Return rows whose first two fields are file path and checksum."""


@dataclass(frozen=True, slots=True)
class MovedEntityFacts:
    """The current path and content checksum of a candidate moved entity."""

    file_path: str
    checksum: str | None


@dataclass(frozen=True, slots=True)
class MoveVacateMarker:
    """The moved entity and source content checksum recorded for a vacated path."""

    entity_id: int | None
    checksum: str | None


class MoveDetectionEntity(Protocol):
    """Minimal entity shape needed to confirm a move orphan by id."""

    @property
    def id(self) -> int:
        """Return the entity's database id."""

    @property
    def file_path(self) -> str:
        """Return the entity's current stored file path."""

    @property
    def checksum(self) -> str | None:
        """Return the entity's stored content checksum."""


class MoveDetectionEntityRepository(Protocol):
    """Repository capability that loads entities by id for move-orphan confirmation."""

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> Sequence[MoveDetectionEntity]:
        """Return the entities for ``ids`` (one batch)."""


class MovedEntitySource(Protocol):
    """Capability that loads the current facts of candidate moved entities by id."""

    async def load_entity_facts_by_id(
        self,
        entity_ids: Sequence[int],
    ) -> Mapping[int, MovedEntityFacts]:
        """Return current (path, checksum) facts for each requested entity id (one batch)."""


class MoveVacateSource(Protocol):
    """Capability that reports and retires move-vacate markers."""

    async def load_vacate_markers(
        self,
        file_paths: Sequence[FileIndexPath],
    ) -> Mapping[FileIndexPath, MoveVacateMarker]:
        """Return the vacate marker for each of ``file_paths`` that carries one."""

    async def clear_vacate_marker(
        self,
        file_path: FileIndexPath,
        file_checksum: FileIndexChecksum,
    ) -> None:
        """Retire the marker only if it still describes ``file_checksum``."""


class MoveVacateMarkerRow(Protocol):
    """Marker fields the repository yields for a vacated path."""

    @property
    def entity_id(self) -> int | None:
        """Return the moved entity id recorded on the marker."""

    @property
    def file_checksum(self) -> str | None:
        """Return the source content checksum recorded on the marker."""


class MoveVacateRepository(Protocol):
    """Repository capability that resolves outstanding move-vacate markers by path."""

    async def load_vacate_markers(
        self,
        session: AsyncSession,
        file_paths: Sequence[str],
    ) -> Mapping[str, MoveVacateMarkerRow]:
        """Return the marker (moved entity id + source checksum) for each marked path."""

    async def clear_vacate(
        self,
        session: AsyncSession,
        *,
        file_path: str,
        file_checksum: str | None,
    ) -> None:
        """Retire the marker only if its source checksum still matches."""


@dataclass(frozen=True, slots=True)
class RepositoryIndexedFileChecksumSource:
    """Load indexed file checksums from the entity repository."""

    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: IndexedFileChecksumRepository

    async def load_indexed_file_checksums(
        self,
        file_paths: Sequence[FileIndexPath],
    ) -> Mapping[FileIndexPath, FileIndexChecksum | None]:
        """Load accepted entity checksums for target paths."""
        async with self.session_maker() as session:
            rows = await self.entity_repository.get_by_file_paths(session, file_paths)
        return {str(row[0]): None if row[1] is None else str(row[1]) for row in rows}


@dataclass(frozen=True, slots=True)
class RepositoryMovedEntitySource:
    """Load current facts of candidate moved entities by id via one batched repository lookup."""

    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: MoveDetectionEntityRepository

    async def load_entity_facts_by_id(
        self,
        entity_ids: Sequence[int],
    ) -> Mapping[int, MovedEntityFacts]:
        """Return current (path, checksum) facts for each requested entity id (one query)."""
        unique = list({int(entity_id) for entity_id in entity_ids})
        if not unique:
            return {}
        async with self.session_maker() as session:
            entities = await self.entity_repository.find_by_ids(session, unique)
        return {
            int(entity.id): MovedEntityFacts(
                file_path=str(entity.file_path),
                checksum=None if entity.checksum is None else str(entity.checksum),
            )
            for entity in entities
        }


@dataclass(frozen=True, slots=True)
class RepositoryMoveVacateSource:
    """Resolve and retire outstanding move-vacate markers."""

    session_maker: async_sessionmaker[AsyncSession]
    vacate_repository: MoveVacateRepository

    async def load_vacate_markers(
        self,
        file_paths: Sequence[FileIndexPath],
    ) -> Mapping[FileIndexPath, MoveVacateMarker]:
        """Return the vacate marker for each marked path (one query)."""
        paths = list(file_paths)
        if not paths:
            return {}
        async with self.session_maker() as session:
            rows = await self.vacate_repository.load_vacate_markers(session, paths)
        return {
            path: MoveVacateMarker(entity_id=row.entity_id, checksum=row.file_checksum)
            for path, row in rows.items()
        }

    async def clear_vacate_marker(
        self,
        file_path: FileIndexPath,
        file_checksum: FileIndexChecksum,
    ) -> None:
        """Retire one marker in its own committed transaction."""
        async with self.session_maker.begin() as session:
            await self.vacate_repository.clear_vacate(
                session,
                file_path=file_path,
                file_checksum=file_checksum,
            )


@dataclass(frozen=True, slots=True)
class _InspectedTarget:
    """One target's base decision plus the facts the move-orphan gate needs."""

    target: FileIndexTarget
    decision: FileIndexDecision
    current_checksum: FileIndexChecksum | None
    row_present: bool


@dataclass(frozen=True, slots=True)
class FileIndexChecker:
    """Plan content reads using indexed and current file checksums."""

    indexed_checksum_source: IndexedFileChecksumSource
    current_checksum_source: CurrentFileChecksumSource
    # Move-orphan gate (#1601). Both sources must be set for the gate to run: a would-be *create*
    # (no DB row owns the path) whose content is already indexed elsewhere AND whose path carries a
    # move-vacate marker is a move's lingering source object — planned `current` instead of `read`
    # so it is not re-imported as a `-1` duplicate. Requiring the vacate marker is what separates a
    # ghost from a legitimate byte-identical copy (which has no marker and stays a new file).
    moved_entity_source: MovedEntitySource | None = None
    move_vacate_source: MoveVacateSource | None = None
    # The move-vacate marker stores the moved note's *content* checksum. When storage freshness
    # keys on a different checksum domain than note content (e.g. cloud indexes by S3 ETag but
    # note content is SHA-256), the gate must compare the marker against the current object's
    # content checksum, not the freshness checksum, or the marker never matches and every orphan
    # is either ignored or wrongly retired (basic-memory-cloud#1601). Left unset when the two
    # domains coincide (local uses one SHA-256 checksum everywhere), in which case the gate falls
    # back to the freshness `current_checksum` and behavior is unchanged.
    move_orphan_checksum_source: CurrentFileChecksumSource | None = None

    async def detect(self, targets: Sequence[FileIndexTarget]) -> FileIndexPlan:
        """Return the file paths whose current storage object still needs indexing."""
        if not targets:
            return FileIndexPlan(paths_to_read=(), decisions=())

        legacy_targets = not any(target.observed_checksum is not None for target in targets)
        if legacy_targets and (self.moved_entity_source is None or self.move_vacate_source is None):
            return plan_legacy_file_index_targets(targets)

        indexed_checksum_by_path = await self.indexed_checksum_source.load_indexed_file_checksums(
            tuple(target.path for target in targets)
        )
        legacy_markers: Mapping[FileIndexPath, MoveVacateMarker] | None = None
        if legacy_targets:
            # Forced-full reads every non-orphan target regardless of freshness. Query the cheap
            # marker table first, then hash only DB-absent paths that could actually be move
            # orphans; the later content reader remains the single checksum pass for all others.
            assert self.move_vacate_source is not None
            legacy_markers = await self.move_vacate_source.load_vacate_markers(
                [target.path for target in targets if target.path not in indexed_checksum_by_path]
            )
        inspected: list[_InspectedTarget] = []
        for target in targets:
            if not legacy_targets:
                decision, current_checksum = await self.inspect_target(
                    target,
                    indexed_checksum=indexed_checksum_by_path.get(target.path),
                )
            else:
                # Forced-full and legacy batches still read every non-orphan target. Load current
                # metadata only so the move-orphan safety gate can recognize a lingering source.
                decision = FileIndexDecision(
                    path=target.path,
                    status=FileIndexDecisionStatus.read,
                    reason=f"legacy target requires indexing: {target.path}",
                )
                current_checksum = (
                    await self.current_checksum_source.load_current_file_checksum(target.path)
                    if legacy_markers is not None and target.path in legacy_markers
                    else None
                )
            inspected.append(
                _InspectedTarget(
                    target=target,
                    decision=decision,
                    current_checksum=current_checksum,
                    # A missing map key means no DB row owns the path (a create); a present key with
                    # a null value is an incomplete row that must still be read/repaired, not skipped.
                    row_present=target.path in indexed_checksum_by_path,
                )
            )

        decisions = await self._apply_move_orphan_gate(
            inspected,
            prefetched_markers=legacy_markers,
        )
        return build_file_index_plan(decisions)

    async def inspect_target(
        self,
        target: FileIndexTarget,
        *,
        indexed_checksum: FileIndexChecksum | None,
    ) -> tuple[FileIndexDecision, FileIndexChecksum | None]:
        """Inspect one file target, returning its decision and the current checksum it read."""
        observed_decision = plan_file_index_target_from_observed(
            target,
            db_checksum=indexed_checksum,
        )
        if observed_decision is not None:
            return observed_decision, None

        current_checksum = await self.current_checksum_source.load_current_file_checksum(
            target.path
        )
        decision = plan_file_index_target_from_current(
            target,
            db_checksum=indexed_checksum,
            current_checksum=current_checksum,
        )
        return decision, current_checksum

    async def _apply_move_orphan_gate(
        self,
        inspected: Sequence[_InspectedTarget],
        *,
        prefetched_markers: Mapping[FileIndexPath, MoveVacateMarker] | None = None,
    ) -> list[FileIndexDecision]:
        """Downgrade a move's leftover source object to `current` in one batch (#1601).

        A create is a candidate only when it would read, no DB row owns the path, and it has a
        current checksum. Such a candidate is a move orphan only when its path carries a vacate
        marker AND the object is still the moved note's content: the current checksum equals the
        marker's recorded source checksum, and either the marker's entity now holds that same
        content at a *different* path or that destination entity has been deleted. A path
        overwritten with different bytes indexes normally and retires the old checksum-backed
        marker, because observing a different source object proves the moved bytes no longer need
        cleanup. A genuine new file or copy without a marker also indexes normally.
        """
        decisions = [item.decision for item in inspected]
        if self.moved_entity_source is None or self.move_vacate_source is None:
            return decisions

        candidates = [
            (index, item)
            for index, item in enumerate(inspected)
            if item.decision.status == FileIndexDecisionStatus.read
            and not item.row_present
            and item.current_checksum is not None
        ]
        if not candidates:
            return decisions

        markers = (
            prefetched_markers
            if prefetched_markers is not None
            else await self.move_vacate_source.load_vacate_markers(
                [item.target.path for _, item in candidates]
            )
        )
        # The marker records the moved note's content checksum. Compare it against the object's
        # content checksum (from move_orphan_checksum_source when the freshness domain differs),
        # falling back to the freshness checksum when the two domains coincide. The moved-entity
        # checksum below stays in the freshness/entity domain, so gap-(a) keeps using current.
        gated_candidates: list[
            tuple[int, _InspectedTarget, MoveVacateMarker, FileIndexChecksum | None]
        ] = []
        for index, item in candidates:
            marker = markers.get(item.target.path)
            if marker is None:
                continue
            content_checksum = (
                await self.move_orphan_checksum_source.load_current_file_checksum(item.target.path)
                if self.move_orphan_checksum_source is not None
                else item.current_checksum
            )
            if marker.checksum is not None and marker.checksum != content_checksum:
                # Trigger: a different source object now occupies the vacated path.
                # Why: even if the process died before cleanup was enqueued, the moved bytes are
                # gone and their marker must not suppress a later legitimate reuse of those bytes.
                # Outcome: conditionally retire only the checksum we observed, preserving any
                # concurrent move that refreshed the marker after this read.
                await self.move_vacate_source.clear_vacate_marker(
                    item.target.path,
                    marker.checksum,
                )
                continue
            gated_candidates.append((index, item, marker, content_checksum))

        marked_entity_ids = [
            marker.entity_id for _, _, marker, _ in gated_candidates if marker.entity_id is not None
        ]
        entity_facts = (
            await self.moved_entity_source.load_entity_facts_by_id(marked_entity_ids)
            if marked_entity_ids
            else {}
        )
        for index, item, marker, content_checksum in gated_candidates:
            moved_entity = (
                entity_facts.get(marker.entity_id) if marker.entity_id is not None else None
            )
            if moved_entity is None:
                # SQLite variants do not always apply ON DELETE SET NULL consistently, so a
                # deleted destination can leave either a null or dangling entity ID. The
                # transactionally recorded source checksum proves both forms are tombstones for
                # these exact bytes; keep suppressing resurrection until guarded cleanup clears
                # the marker. Without that checksum proof, the file remains a normal create.
                if marker.checksum is None or marker.checksum != content_checksum:
                    continue
                decisions[index] = move_orphan_file_index_decision(
                    item.target.path,
                    indexed_at=None,
                )
                continue
            if moved_entity.file_path == item.target.path:
                # The recorded entity never actually moved off this path, so a stale/spurious
                # marker must not lose content.
                continue
            if marker.checksum is None and moved_entity.checksum != item.current_checksum:
                # Source checksum was unknown at move time (gap (a)): fall back to confirming the
                # object is the moved entity's current content before treating it as the leftover.
                # Both sides are the entity/freshness domain, so this uses current, not content.
                continue
            decisions[index] = move_orphan_file_index_decision(
                item.target.path, indexed_at=moved_entity.file_path
            )
        return decisions
