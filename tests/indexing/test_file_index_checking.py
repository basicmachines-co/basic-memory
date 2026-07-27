"""Tests for portable file-index metadata checking."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.file_index_checking import (
    FileIndexChecker,
    MoveVacateMarker,
    MovedEntityFacts,
    RepositoryIndexedFileChecksumSource,
    RepositoryMoveVacateSource,
    RepositoryMovedEntitySource,
)
from basic_memory.indexing.file_index_planning import FileIndexDecisionStatus, FileIndexTarget


@dataclass(slots=True)
class FakeSessionContext:
    session: object

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        return None


@dataclass(slots=True)
class FakeSessionMaker:
    session: object

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(session=self.session)

    def begin(self) -> FakeSessionContext:
        return FakeSessionContext(session=self.session)


@dataclass(slots=True)
class RecordingChecksumRepository:
    rows: list[tuple[object, object | None]]
    calls: list[tuple[object, tuple[str, ...]]] = field(default_factory=list)

    async def get_by_file_paths(
        self,
        session: AsyncSession,
        file_paths: Sequence[str],
    ) -> list[tuple[object, object | None]]:
        self.calls.append((session, tuple(file_paths)))
        return self.rows


class StubIndexedChecksumSource:
    """Returns accepted checksums for indexed paths."""

    def __init__(self, checksums_by_path: dict[str, str | None]) -> None:
        self.checksums_by_path = checksums_by_path
        self.requested_paths: list[tuple[str, ...]] = []

    async def load_indexed_file_checksums(
        self,
        file_paths: Sequence[str],
    ) -> dict[str, str | None]:
        self.requested_paths.append(tuple(file_paths))
        return {
            file_path: self.checksums_by_path[file_path]
            for file_path in file_paths
            if file_path in self.checksums_by_path
        }


class StubCurrentChecksumSource:
    """Returns current storage checksums for paths that need live metadata."""

    def __init__(self, checksums_by_path: dict[str, str | None]) -> None:
        self.checksums_by_path = checksums_by_path
        self.requested_paths: list[str] = []

    async def load_current_file_checksum(self, file_path: str) -> str | None:
        self.requested_paths.append(file_path)
        return self.checksums_by_path[file_path]


@dataclass(slots=True)
class StubMovedEntitySource:
    """Loads current (path, checksum) facts for moved entities by id (batched)."""

    facts_by_id: dict[int, MovedEntityFacts] = field(default_factory=dict)
    calls: list[tuple[int, ...]] = field(default_factory=list)

    async def load_entity_facts_by_id(
        self, entity_ids: Sequence[int]
    ) -> dict[int, MovedEntityFacts]:
        self.calls.append(tuple(entity_ids))
        return {eid: self.facts_by_id[eid] for eid in entity_ids if eid in self.facts_by_id}


@dataclass(slots=True)
class StubMoveVacateSource:
    """Returns the move-vacate marker recorded for each of the given paths."""

    markers: dict[str, MoveVacateMarker] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    clear_calls: list[tuple[str, str]] = field(default_factory=list)

    async def load_vacate_markers(self, file_paths: Sequence[str]) -> dict[str, MoveVacateMarker]:
        self.calls.append(tuple(file_paths))
        return {p: self.markers[p] for p in file_paths if p in self.markers}

    async def clear_vacate_marker(self, file_path: str, file_checksum: str) -> None:
        self.clear_calls.append((file_path, file_checksum))


@dataclass(frozen=True, slots=True)
class FakeEntity:
    id: int
    file_path: str
    checksum: str | None


@dataclass(slots=True)
class FakeEntityRepository:
    entities: list[FakeEntity]
    calls: list[tuple[object, tuple[int, ...]]] = field(default_factory=list)

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> list[FakeEntity]:
        self.calls.append((session, tuple(ids)))
        return [entity for entity in self.entities if entity.id in ids]


@dataclass(slots=True)
class FakeVacateMarkerRow:
    entity_id: int
    file_checksum: str | None


@dataclass(slots=True)
class FakeVacateRepository:
    markers: dict[str, FakeVacateMarkerRow]
    calls: list[tuple[object, tuple[str, ...]]] = field(default_factory=list)
    clear_calls: list[tuple[object, str, str | None]] = field(default_factory=list)

    async def load_vacate_markers(
        self,
        session: AsyncSession,
        file_paths: Sequence[str],
    ) -> dict[str, FakeVacateMarkerRow]:
        self.calls.append((session, tuple(file_paths)))
        return {p: self.markers[p] for p in file_paths if p in self.markers}

    async def clear_vacate(
        self,
        session: AsyncSession,
        *,
        file_path: str,
        file_checksum: str | None,
    ) -> None:
        self.clear_calls.append((session, file_path, file_checksum))


@pytest.mark.asyncio
async def test_checker_skips_current_files_before_content_reads() -> None:
    indexed_source = StubIndexedChecksumSource(
        {
            "notes/current.md": "etag-current",
            "notes/caught-up.md": "etag-caught-up",
            "notes/dirty.md": "old-etag",
            "notes/missing.md": "old-etag",
        }
    )
    current_source = StubCurrentChecksumSource(
        {
            "notes/caught-up.md": "etag-caught-up",
            "notes/dirty.md": "etag-dirty",
            "notes/missing.md": None,
        }
    )

    plan = await FileIndexChecker(
        indexed_checksum_source=indexed_source,
        current_checksum_source=current_source,
    ).detect(
        [
            FileIndexTarget(path="notes/current.md", observed_checksum="etag-current"),
            FileIndexTarget(path="notes/caught-up.md", observed_checksum="old-observed"),
            FileIndexTarget(path="notes/dirty.md", observed_checksum="etag-dirty"),
            FileIndexTarget(path="notes/missing.md", observed_checksum="etag-missing"),
        ]
    )

    assert plan.paths_to_read == ("notes/dirty.md",)
    assert [(decision.path, decision.status) for decision in plan.decisions] == [
        ("notes/current.md", FileIndexDecisionStatus.current),
        ("notes/caught-up.md", FileIndexDecisionStatus.current),
        ("notes/missing.md", FileIndexDecisionStatus.missing),
    ]
    assert indexed_source.requested_paths == [
        (
            "notes/current.md",
            "notes/caught-up.md",
            "notes/dirty.md",
            "notes/missing.md",
        )
    ]
    assert current_source.requested_paths == [
        "notes/caught-up.md",
        "notes/dirty.md",
        "notes/missing.md",
    ]


@pytest.mark.asyncio
async def test_checker_reads_legacy_targets_without_metadata() -> None:
    indexed_source = StubIndexedChecksumSource({})
    current_source = StubCurrentChecksumSource({})

    plan = await FileIndexChecker(
        indexed_checksum_source=indexed_source,
        current_checksum_source=current_source,
    ).detect([FileIndexTarget(path="notes/legacy.md")])

    assert plan.paths_to_read == ("notes/legacy.md",)
    assert plan.decisions == ()
    assert indexed_source.requested_paths == []
    assert current_source.requested_paths == []


@pytest.mark.asyncio
async def test_checker_returns_empty_plan_without_sources_for_empty_targets() -> None:
    indexed_source = StubIndexedChecksumSource({})
    current_source = StubCurrentChecksumSource({})

    plan = await FileIndexChecker(
        indexed_checksum_source=indexed_source,
        current_checksum_source=current_source,
    ).detect([])

    assert plan.paths_to_read == ()
    assert plan.decisions == ()
    assert indexed_source.requested_paths == []
    assert current_source.requested_paths == []


@pytest.mark.asyncio
async def test_repository_indexed_file_checksum_source_maps_repository_rows() -> None:
    session = object()
    repository = RecordingChecksumRepository(
        rows=[
            ("notes/a.md", "etag-a"),
            ("notes/b.md", None),
        ]
    )
    source = RepositoryIndexedFileChecksumSource(
        session_maker=cast(async_sessionmaker[AsyncSession], FakeSessionMaker(session)),
        entity_repository=repository,
    )

    checksums = await source.load_indexed_file_checksums(["notes/a.md", "notes/b.md"])

    assert checksums == {
        "notes/a.md": "etag-a",
        "notes/b.md": None,
    }
    assert repository.calls == [(session, ("notes/a.md", "notes/b.md"))]


def _orphan_checker(
    *,
    indexed: dict[str, str | None],
    current: dict[str, str | None],
    markers: dict[str, MoveVacateMarker],
    entity_facts: dict[int, MovedEntityFacts],
) -> tuple[FileIndexChecker, StubMovedEntitySource, StubMoveVacateSource]:
    moved = StubMovedEntitySource(facts_by_id=entity_facts)
    vacate = StubMoveVacateSource(markers=markers)
    checker = FileIndexChecker(
        indexed_checksum_source=StubIndexedChecksumSource(indexed),
        current_checksum_source=StubCurrentChecksumSource(current),
        moved_entity_source=moved,
        move_vacate_source=vacate,
    )
    return checker, moved, vacate


@pytest.mark.asyncio
async def test_checker_forced_full_hashes_only_marked_create_candidates() -> None:
    """Forced-full keeps its one content-read pass for paths with no move evidence."""
    current = StubCurrentChecksumSource({"notes/marked.md": "vacated-checksum"})
    moved = StubMovedEntitySource(
        facts_by_id={
            42: MovedEntityFacts(
                file_path="archive/marked.md",
                checksum="vacated-checksum",
            )
        }
    )
    vacate = StubMoveVacateSource(
        markers={
            "notes/marked.md": MoveVacateMarker(
                entity_id=42,
                checksum="vacated-checksum",
            )
        }
    )
    checker = FileIndexChecker(
        indexed_checksum_source=StubIndexedChecksumSource(
            {"notes/existing.md": "accepted-checksum"}
        ),
        current_checksum_source=current,
        moved_entity_source=moved,
        move_vacate_source=vacate,
    )

    plan = await checker.detect(
        [
            FileIndexTarget(path="notes/marked.md"),
            FileIndexTarget(path="notes/unmarked.md"),
            FileIndexTarget(path="notes/existing.md"),
        ]
    )

    assert plan.paths_to_read == ("notes/unmarked.md", "notes/existing.md")
    assert current.requested_paths == ["notes/marked.md"]
    assert vacate.calls == [("notes/marked.md", "notes/unmarked.md")]
    assert moved.calls == [(42,)]


@pytest.mark.asyncio
async def test_checker_skips_leftover_source_of_a_move() -> None:
    """The lingering source object of a move is planned `current`, not re-imported (#1601).

    Gate condition: no DB row owns the path, its content checksum matches the vacate marker's
    recorded source checksum, and the marker's entity now holds that same content at another path.
    """
    checker, moved, vacate = _orphan_checker(
        indexed={},  # no entity owns the source path -> would create
        current={"koncept/note.md": "checksum-shared"},
        markers={"koncept/note.md": MoveVacateMarker(entity_id=42, checksum="checksum-shared")},
        entity_facts={42: MovedEntityFacts(file_path="arkiv/note.md", checksum="checksum-shared")},
    )

    plan = await checker.detect(
        [FileIndexTarget(path="koncept/note.md", observed_checksum="checksum-shared")]
    )

    assert plan.paths_to_read == ()
    assert [(d.path, d.status) for d in plan.decisions] == [
        ("koncept/note.md", FileIndexDecisionStatus.current),
    ]
    assert vacate.calls == [("koncept/note.md",)]
    assert moved.calls == [(42,)]


@pytest.mark.asyncio
async def test_checker_gate_uses_content_checksum_source_when_domains_differ() -> None:
    """The gate compares the marker against a distinct content-checksum source when set (#1601).

    Cloud freshness keys on the S3 ETag while note content (and the marker) use a SHA-256 content
    checksum. With move_orphan_checksum_source wired, the gate must compare the marker against the
    content checksum, not the ETag freshness checksum — otherwise the marker never matches and the
    leftover source is wrongly retired and re-indexed as a ghost.
    """
    content_source = StubCurrentChecksumSource({"koncept/note.md": "sha-content"})
    moved = StubMovedEntitySource(
        facts_by_id={42: MovedEntityFacts(file_path="arkiv/note.md", checksum="etag-freshness")}
    )
    vacate = StubMoveVacateSource(
        markers={"koncept/note.md": MoveVacateMarker(entity_id=42, checksum="sha-content")}
    )
    checker = FileIndexChecker(
        indexed_checksum_source=StubIndexedChecksumSource({}),
        # Freshness domain (ETag) differs from the marker's content checksum on purpose.
        current_checksum_source=StubCurrentChecksumSource({"koncept/note.md": "etag-freshness"}),
        moved_entity_source=moved,
        move_vacate_source=vacate,
        move_orphan_checksum_source=content_source,
    )

    plan = await checker.detect(
        [FileIndexTarget(path="koncept/note.md", observed_checksum="etag-freshness")]
    )

    assert plan.paths_to_read == ()
    assert [(d.path, d.status) for d in plan.decisions] == [
        ("koncept/note.md", FileIndexDecisionStatus.current),
    ]
    # The gate consulted the content source and did NOT retire the marker on a domain mismatch.
    assert content_source.requested_paths == ["koncept/note.md"]
    assert vacate.clear_calls == []


@pytest.mark.asyncio
async def test_checker_indexes_replacement_note_at_a_vacated_path() -> None:
    """A vacated path overwritten with a DIFFERENT note's bytes is a legit replacement, not a ghost.

    The current checksum no longer matches the marker's source checksum, so it is indexed as new
    even though a checksum twin exists — the P1 false-skip Codex flagged.
    """
    checker, moved, vacate = _orphan_checker(
        indexed={},
        current={"koncept/note.md": "other-note-checksum"},
        markers={"koncept/note.md": MoveVacateMarker(entity_id=42, checksum="original-checksum")},
        # The other note (a real twin) exists, but it is not the marker's entity.
        entity_facts={
            42: MovedEntityFacts(file_path="arkiv/note.md", checksum="original-checksum")
        },
    )

    plan = await checker.detect(
        [FileIndexTarget(path="koncept/note.md", observed_checksum="other-note-checksum")]
    )

    assert plan.paths_to_read == ("koncept/note.md",)
    assert vacate.clear_calls == [("koncept/note.md", "original-checksum")]
    assert moved.calls == []


@pytest.mark.asyncio
async def test_checker_skips_leftover_when_marker_checksum_unknown() -> None:
    """A move accepted before its source was materialized records a null-checksum marker.

    The gap-(a) case (basic-memory-cloud#1601): the source checksum was unknown at move time, so
    the gate falls back to confirming the object is the moved entity's current content. The
    lingering source must still be skipped, not minted as a duplicate.
    """
    checker, _, _ = _orphan_checker(
        indexed={},
        current={"koncept/note.md": "content-ck"},
        markers={"koncept/note.md": MoveVacateMarker(entity_id=42, checksum=None)},
        entity_facts={42: MovedEntityFacts(file_path="arkiv/note.md", checksum="content-ck")},
    )

    plan = await checker.detect(
        [FileIndexTarget(path="koncept/note.md", observed_checksum="content-ck")]
    )

    assert plan.paths_to_read == ()
    assert [(d.path, d.status) for d in plan.decisions] == [
        ("koncept/note.md", FileIndexDecisionStatus.current),
    ]


@pytest.mark.asyncio
async def test_checker_indexes_overwrite_of_null_checksum_marker_path() -> None:
    """A null-checksum marker still does not gate a path overwritten with different content."""
    checker, _, _ = _orphan_checker(
        indexed={},
        current={"koncept/note.md": "different-content"},
        markers={"koncept/note.md": MoveVacateMarker(entity_id=42, checksum=None)},
        entity_facts={42: MovedEntityFacts(file_path="arkiv/note.md", checksum="moved-content")},
    )

    plan = await checker.detect(
        [FileIndexTarget(path="koncept/note.md", observed_checksum="different-content")]
    )

    assert plan.paths_to_read == ("koncept/note.md",)


@pytest.mark.asyncio
async def test_checker_skips_checksum_backed_tombstone_after_moved_entity_delete() -> None:
    """Deleting the moved entity does not let its still-present source resurrect."""
    checker, moved, _ = _orphan_checker(
        indexed={},
        current={"koncept/deleted.md": "vacated-content"},
        markers={
            "koncept/deleted.md": MoveVacateMarker(
                entity_id=None,
                checksum="vacated-content",
            )
        },
        entity_facts={},
    )

    plan = await checker.detect(
        [FileIndexTarget(path="koncept/deleted.md", observed_checksum="vacated-content")]
    )

    assert plan.paths_to_read == ()
    assert [(decision.path, decision.status) for decision in plan.decisions] == [
        ("koncept/deleted.md", FileIndexDecisionStatus.current)
    ]
    assert moved.calls == []


@pytest.mark.asyncio
async def test_checker_does_not_trust_null_checksum_tombstone() -> None:
    """A tombstone without either an entity or checksum cannot identify the source bytes."""
    checker, _, _ = _orphan_checker(
        indexed={},
        current={"koncept/unknown.md": "current-content"},
        markers={"koncept/unknown.md": MoveVacateMarker(entity_id=None, checksum=None)},
        entity_facts={},
    )

    plan = await checker.detect(
        [FileIndexTarget(path="koncept/unknown.md", observed_checksum="current-content")]
    )

    assert plan.paths_to_read == ("koncept/unknown.md",)


@pytest.mark.asyncio
async def test_checker_indexes_byte_identical_copy_without_marker() -> None:
    """A byte-identical copy has no vacate marker, so it is indexed as a new note."""
    checker, _, _ = _orphan_checker(
        indexed={},
        current={"notes/copy.md": "shared"},
        markers={},  # no move vacated this path
        entity_facts={7: MovedEntityFacts(file_path="notes/original.md", checksum="shared")},
    )

    plan = await checker.detect([FileIndexTarget(path="notes/copy.md", observed_checksum="shared")])

    assert plan.paths_to_read == ("notes/copy.md",)


@pytest.mark.asyncio
async def test_checker_handles_dangling_marker_entity_and_entity_still_at_source() -> None:
    """A dangling destination ID is a tombstone, but an entity still at source is not."""
    # SQLite can retain the deleted entity ID instead of applying ON DELETE SET NULL. The
    # checksum-backed marker still proves these exact bytes are the vacated source.
    checker, _, _ = _orphan_checker(
        indexed={},
        current={"koncept/gone.md": "ck"},
        markers={"koncept/gone.md": MoveVacateMarker(entity_id=42, checksum="ck")},
        entity_facts={},  # entity 42 not found
    )
    plan = await checker.detect([FileIndexTarget(path="koncept/gone.md", observed_checksum="ck")])
    assert plan.paths_to_read == ()
    assert [(decision.path, decision.status) for decision in plan.decisions] == [
        ("koncept/gone.md", FileIndexDecisionStatus.current)
    ]

    # Marker entity still owns THIS path (not actually moved away) -> do not skip.
    checker2, _, _ = _orphan_checker(
        indexed={},
        current={"koncept/here.md": "ck"},
        markers={"koncept/here.md": MoveVacateMarker(entity_id=42, checksum="ck")},
        entity_facts={42: MovedEntityFacts(file_path="koncept/here.md", checksum="ck")},
    )
    plan2 = await checker2.detect([FileIndexTarget(path="koncept/here.md", observed_checksum="ck")])
    assert plan2.paths_to_read == ("koncept/here.md",)


@pytest.mark.asyncio
async def test_checker_does_not_gate_updates_or_null_checksum_rows() -> None:
    """An existing row (even one with a null checksum) is an edit/repair, never move-gated.

    A present-but-null indexed checksum is an incomplete row that must still be read, not skipped
    as a create — the gate keys on row *absence*, not on a null value.
    """
    checker, moved, vacate = _orphan_checker(
        indexed={"notes/edited.md": "old", "notes/pending.md": None},
        current={"notes/edited.md": "new", "notes/pending.md": "materialized"},
        markers={
            "notes/edited.md": MoveVacateMarker(entity_id=1, checksum="new"),
            "notes/pending.md": MoveVacateMarker(entity_id=2, checksum="materialized"),
        },
        entity_facts={
            1: MovedEntityFacts(file_path="x.md", checksum="new"),
            2: MovedEntityFacts(file_path="y.md", checksum="materialized"),
        },
    )

    plan = await checker.detect(
        [
            FileIndexTarget(path="notes/edited.md", observed_checksum="new"),
            FileIndexTarget(path="notes/pending.md", observed_checksum="materialized"),
        ]
    )

    assert set(plan.paths_to_read) == {"notes/edited.md", "notes/pending.md"}
    # No create candidates -> move detection is never consulted.
    assert moved.calls == []
    assert vacate.calls == []


@pytest.mark.asyncio
async def test_checker_batches_move_detection_across_create_candidates() -> None:
    """Multiple create candidates share one marker query and one entity query (no per-file round-trips)."""
    checker, moved, vacate = _orphan_checker(
        indexed={},
        current={"a.md": "ck-a", "b.md": "ck-b", "c.md": "ck-c"},
        markers={
            "a.md": MoveVacateMarker(entity_id=1, checksum="ck-a"),  # orphan -> skip
            "b.md": MoveVacateMarker(entity_id=2, checksum="mismatch"),  # marker != current -> read
            # c.md has no marker -> read
        },
        entity_facts={
            1: MovedEntityFacts(file_path="dest-a.md", checksum="ck-a"),
            2: MovedEntityFacts(file_path="dest-b.md", checksum="ck-b"),
        },
    )

    plan = await checker.detect(
        [
            FileIndexTarget(path="a.md", observed_checksum="ck-a"),
            FileIndexTarget(path="b.md", observed_checksum="ck-b"),
            FileIndexTarget(path="c.md", observed_checksum="ck-c"),
        ]
    )

    assert set(plan.paths_to_read) == {"b.md", "c.md"}
    assert [(d.path, d.status) for d in plan.decisions] == [
        ("a.md", FileIndexDecisionStatus.current),
    ]
    # One marker query over all three candidates; the checksum mismatch is retired before the
    # entity lookup, so only the candidate that can still be gated needs entity facts.
    assert len(vacate.calls) == 1
    assert set(vacate.calls[0]) == {"a.md", "b.md", "c.md"}
    assert vacate.clear_calls == [("b.md", "mismatch")]
    assert len(moved.calls) == 1
    assert moved.calls[0] == (1,)


@pytest.mark.asyncio
async def test_checker_gate_inert_without_both_sources() -> None:
    """The gate never fires unless both sources are configured."""
    plan = await FileIndexChecker(
        indexed_checksum_source=StubIndexedChecksumSource({}),
        current_checksum_source=StubCurrentChecksumSource({"koncept/note.md": "ck"}),
        move_vacate_source=StubMoveVacateSource(
            markers={"koncept/note.md": MoveVacateMarker(entity_id=42, checksum="ck")}
        ),
        # moved_entity_source omitted
    ).detect([FileIndexTarget(path="koncept/note.md", observed_checksum="ck")])

    assert plan.paths_to_read == ("koncept/note.md",)


@pytest.mark.asyncio
async def test_repository_moved_entity_source_loads_facts_by_id() -> None:
    session = object()
    session_maker = cast(async_sessionmaker[AsyncSession], FakeSessionMaker(session))

    repo = FakeEntityRepository(
        [
            FakeEntity(id=1, file_path="arkiv/note.md", checksum="ck-1"),
            FakeEntity(id=2, file_path="arkiv/other.md", checksum=None),
        ]
    )
    source = RepositoryMovedEntitySource(session_maker=session_maker, entity_repository=repo)

    facts = await source.load_entity_facts_by_id([1, 2, 1])
    assert facts == {
        1: MovedEntityFacts(file_path="arkiv/note.md", checksum="ck-1"),
        2: MovedEntityFacts(file_path="arkiv/other.md", checksum=None),
    }
    # One batched query over the deduped ids.
    assert len(repo.calls) == 1
    assert set(repo.calls[0][1]) == {1, 2}
    assert await source.load_entity_facts_by_id([]) == {}


@pytest.mark.asyncio
async def test_repository_move_vacate_source_delegates_to_repository() -> None:
    session = object()
    session_maker = cast(async_sessionmaker[AsyncSession], FakeSessionMaker(session))
    repo = FakeVacateRepository(
        markers={"koncept/note.md": FakeVacateMarkerRow(entity_id=42, file_checksum="ck")}
    )

    source = RepositoryMoveVacateSource(session_maker=session_maker, vacate_repository=repo)

    markers = await source.load_vacate_markers(["koncept/note.md", "other.md"])
    assert markers == {"koncept/note.md": MoveVacateMarker(entity_id=42, checksum="ck")}
    assert await source.load_vacate_markers([]) == {}
    await source.clear_vacate_marker("koncept/note.md", "ck")
    assert repo.calls == [(session, ("koncept/note.md", "other.md"))]
    assert repo.clear_calls == [(session, "koncept/note.md", "ck")]
