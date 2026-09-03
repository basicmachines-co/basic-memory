"""Unit coverage for the project readiness contract (#1414).

The end-to-end proof lives in `tests/cli/test_project_add_indexing.py`. These
pin the parts of the contract a CLI run cannot reach cheaply: the phase algebra,
the honest state wording, and the per-stage counts against a real database.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.models import Project
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository.search_repository import create_search_repository
from basic_memory.repository.search_repository_base import VECTOR_HYDRATION_BATCH_SIZE
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
)
from basic_memory.repository.semantic_vector_index_factory import (
    resolve_semantic_vector_index_name,
)
from basic_memory.runtime.jobs import RuntimeObservedIndexFile
from basic_memory.schemas.project_readiness import (
    ProjectIndexPhase,
    ProjectIndexReadiness,
    ProjectIndexStage,
    ProjectIndexStageName,
    combine_index_phases,
)
from basic_memory.services.project_readiness import (
    ProjectReadinessService,
    file_stage_counts,
)


def _stage(
    name: ProjectIndexStageName,
    phase: ProjectIndexPhase,
    pending: int = 0,
    total: int = 0,
) -> ProjectIndexStage:
    return ProjectIndexStage(name=name, phase=phase, pending=pending, total=total)


# --- Phase algebra ---


def test_never_indexed_dominates_every_other_phase():
    """Nothing downstream of "we never looked" can be trusted, idle stages included."""
    assert (
        combine_index_phases(
            [ProjectIndexPhase.IDLE, ProjectIndexPhase.NEVER_INDEXED, ProjectIndexPhase.PENDING]
        )
        is ProjectIndexPhase.NEVER_INDEXED
    )


def test_one_pending_stage_makes_the_project_pending():
    assert (
        combine_index_phases([ProjectIndexPhase.IDLE, ProjectIndexPhase.PENDING])
        is ProjectIndexPhase.PENDING
    )


def test_all_idle_stages_make_the_project_idle():
    assert (
        combine_index_phases([ProjectIndexPhase.IDLE, ProjectIndexPhase.IDLE])
        is ProjectIndexPhase.IDLE
    )


def test_no_stages_is_idle():
    """A project with nothing to settle is settled, not stuck."""
    assert combine_index_phases([]) is ProjectIndexPhase.IDLE


# --- Honest state wording ---


def _readiness(
    phase: ProjectIndexPhase,
    *,
    files_on_disk: int,
    indexed_entities: int = 0,
    files_pending: int = 0,
    files_total: int = 0,
) -> ProjectIndexReadiness:
    return ProjectIndexReadiness(
        phase=phase,
        last_indexed_at=None if phase is ProjectIndexPhase.NEVER_INDEXED else datetime.now(UTC),
        files_on_disk=files_on_disk,
        indexed_entities=indexed_entities,
        stages=(
            _stage(ProjectIndexStageName.FILES, phase, files_pending, files_total),
            _stage(ProjectIndexStageName.RELATIONS, phase),
            _stage(ProjectIndexStageName.EMBEDDINGS, phase),
        ),
    )


def test_never_indexed_with_files_names_the_count_and_the_remedy():
    """The exact line the issue asked for, instead of silent emptiness."""
    readiness = _readiness(
        ProjectIndexPhase.NEVER_INDEXED, files_on_disk=25, files_pending=25, files_total=25
    )

    described = readiness.describe("research")

    assert "25 files present, not yet indexed" in described
    assert "bm project index research" in described


def test_never_indexed_with_no_files_does_not_claim_files_are_waiting():
    readiness = _readiness(ProjectIndexPhase.NEVER_INDEXED, files_on_disk=0)

    described = readiness.describe("research")

    assert "no files present" in described
    assert "0 files present" not in described


def test_a_single_file_is_described_in_the_singular():
    readiness = _readiness(
        ProjectIndexPhase.NEVER_INDEXED, files_on_disk=1, files_pending=1, files_total=1
    )

    assert "1 file present, not yet indexed" in readiness.describe("research")


def test_pending_names_which_stages_are_outstanding():
    readiness = ProjectIndexReadiness(
        phase=ProjectIndexPhase.PENDING,
        last_indexed_at=datetime.now(UTC),
        files_on_disk=4,
        indexed_entities=3,
        stages=(
            _stage(ProjectIndexStageName.FILES, ProjectIndexPhase.IDLE, 0, 4),
            _stage(ProjectIndexStageName.RELATIONS, ProjectIndexPhase.PENDING, 2, 9),
            _stage(ProjectIndexStageName.EMBEDDINGS, ProjectIndexPhase.IDLE, 0, 4),
        ),
    )

    described = readiness.describe("research")

    assert "4/4 files current" in described
    assert "relations 2" in described
    # A settled stage is not listed as pending work.
    assert "embeddings" not in described


def test_idle_reports_what_was_indexed():
    readiness = _readiness(
        ProjectIndexPhase.IDLE, files_on_disk=2, indexed_entities=2, files_total=2
    )

    assert "indexed and settled" in readiness.describe("research")
    assert "2 notes from 2 files" in readiness.describe("research")


def test_stage_lookup_returns_the_named_stage():
    readiness = _readiness(ProjectIndexPhase.IDLE, files_on_disk=1, files_total=1)

    assert readiness.stage(ProjectIndexStageName.RELATIONS).name is ProjectIndexStageName.RELATIONS


def test_completed_never_goes_negative():
    """A pending delete can exceed the observed count without inverting the bar."""
    stage = _stage(ProjectIndexStageName.FILES, ProjectIndexPhase.PENDING, pending=5, total=2)

    assert stage.completed == 0


# --- File stage counting ---


def test_unindexed_files_are_pending():
    total, pending = file_stage_counts(
        [
            RuntimeObservedIndexFile(path="a.md", checksum="aaa"),
            RuntimeObservedIndexFile(path="b.md", checksum="bbb"),
        ],
        {},
    )

    assert (total, pending) == (2, 2)


def test_matching_checksums_are_settled():
    total, pending = file_stage_counts(
        [RuntimeObservedIndexFile(path="a.md", checksum="aaa")],
        {"a.md": "aaa"},
    )

    assert (total, pending) == (1, 0)


def test_a_changed_file_is_pending_again():
    total, pending = file_stage_counts(
        [RuntimeObservedIndexFile(path="a.md", checksum="changed")],
        {"a.md": "aaa"},
    )

    assert (total, pending) == (1, 1)


def test_an_unreadable_file_counts_as_pending_not_current():
    """The observer carries unreadable files through with no checksum.

    Unknown is not the same as current: treating it as settled would let a file
    the scan could not read look indexed.
    """
    total, pending = file_stage_counts([RuntimeObservedIndexFile(path="a.md")], {"a.md": "aaa"})

    assert (total, pending) == (1, 1)


def test_an_indexed_file_gone_from_disk_is_pending_work():
    total, pending = file_stage_counts([], {"a.md": "aaa"})

    assert (total, pending) == (1, 1)


def test_total_spans_the_union_so_progress_never_reads_backwards():
    total, pending = file_stage_counts(
        [RuntimeObservedIndexFile(path="new.md", checksum="nnn")],
        {"gone.md": "ggg"},
    )

    assert total == 2
    assert pending == 2


# --- Against a real database ---


@pytest.fixture
def readiness_service(engine_factory, app_config) -> ProjectReadinessService:
    _, session_maker = engine_factory
    return ProjectReadinessService(session_maker=session_maker, app_config=app_config)


@pytest.mark.asyncio
async def test_a_project_with_no_recorded_pass_reports_never_indexed(
    readiness_service, test_project
):
    """The whole point: zero pending work does not mean ready."""
    readiness = await readiness_service.readiness_for(test_project, ())

    assert readiness.phase is ProjectIndexPhase.NEVER_INDEXED
    assert readiness.last_indexed_at is None
    assert all(stage.phase is ProjectIndexPhase.NEVER_INDEXED for stage in readiness.stages)


@pytest.mark.asyncio
async def test_a_recorded_pass_with_nothing_outstanding_reports_idle(
    readiness_service, test_project, engine_factory
):
    """Same zero counts as above; only the recorded pass differs."""
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("UPDATE project SET last_indexed_at = :now WHERE id = :id"),
            {"now": datetime.now(UTC), "id": test_project.id},
        )
        refreshed = await session.get(Project, test_project.id)
        assert refreshed is not None
        session.expunge(refreshed)

    readiness = await readiness_service.readiness_for(refreshed, ())

    assert readiness.phase is ProjectIndexPhase.IDLE
    assert readiness.last_indexed_at is not None
    assert all(stage.phase is ProjectIndexPhase.IDLE for stage in readiness.stages)


@pytest.mark.asyncio
async def test_readiness_for_missing_project_id_fails_loudly(readiness_service):
    """A bad id is a caller bug, not a project that happens to be unready."""
    with pytest.raises(ValueError, match="not found"):
        await readiness_service.readiness_for_project_id(987654, ())


@pytest.mark.asyncio
async def test_embedding_stage_settles_immediately_when_semantic_search_is_off(
    readiness_service, test_project, app_config
):
    """With embeddings disabled there is nothing to wait for, so nothing is owed.

    Reporting outstanding embedding work here would park every project in
    PENDING forever -- the vacuous-ready bug inverted.
    """
    assert app_config.semantic_search_enabled is False

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    assert embeddings.total == 0
    assert embeddings.pending == 0


@pytest.mark.asyncio
async def test_readiness_for_project_id_loads_the_project_itself(readiness_service, test_project):
    """The status route names a project by id and never holds the row."""
    readiness = await readiness_service.readiness_for_project_id(test_project.id, ())

    assert readiness.phase is ProjectIndexPhase.NEVER_INDEXED


@pytest.mark.asyncio
async def test_an_unembedded_markdown_note_is_pending_embedding_work(
    readiness_service, test_project, sample_entity, app_config
):
    """A note with no ready chunk is embedding work a caller can wait on."""
    app_config.semantic_search_enabled = True

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    assert embeddings.total == 1
    assert embeddings.pending == 1


async def _insert_chunk(
    session_maker,
    *,
    entity_id: int,
    project_id: int,
    app_config,
    embedding_model: str | None = None,
    vector_index: str | None = None,
    embedding_status: str = "ready",
) -> None:
    """Insert one manifest row, defaulting to the configured retrieval identity."""
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks "
                "(entity_id, project_id, chunk_key, chunk_text, source_hash, "
                " entity_fingerprint, embedding_model, vector_index, embedding_status) "
                "VALUES (:entity_id, :project_id, 'k', 't', 'h', 'f', "
                " :embedding_model, :vector_index, :embedding_status)"
            ),
            {
                "entity_id": entity_id,
                "project_id": project_id,
                "embedding_model": (
                    embedding_model
                    if embedding_model is not None
                    else configured_embedding_provider_identity(app_config)
                ),
                "vector_index": (
                    vector_index
                    if vector_index is not None
                    else resolve_semantic_vector_index_name(app_config, app_config.database_backend)
                ),
                "embedding_status": embedding_status,
            },
        )


@pytest.mark.asyncio
async def test_a_ready_chunk_under_the_configured_identity_settles_the_stage(
    readiness_service, test_project, sample_entity, app_config, engine_factory
):
    _, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    await _insert_chunk(
        session_maker,
        entity_id=sample_entity.id,
        project_id=test_project.id,
        app_config=app_config,
    )

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    assert embeddings.total == 1
    assert embeddings.pending == 0


@pytest.mark.asyncio
async def test_a_chunk_from_a_previous_embedding_model_is_not_settled(
    readiness_service, test_project, sample_entity, app_config, engine_factory
):
    """A row retrieval will skip must not be counted as embedded.

    Changing the embedding model leaves every chunk `ready` under the old
    identity. Vector hydration admits only the configured one, so counting these
    would report IDLE while semantic search returns nothing — a count that is
    unambiguous and wrong, which is the failure this PR exists to remove.
    """
    _, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    await _insert_chunk(
        session_maker,
        entity_id=sample_entity.id,
        project_id=test_project.id,
        app_config=app_config,
        embedding_model="SomeOtherProvider:retired-model:384",
    )

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    assert embeddings.total == 1
    assert embeddings.pending == 1


@pytest.mark.asyncio
async def test_a_chunk_from_a_previous_vector_index_is_not_settled(
    readiness_service, test_project, sample_entity, app_config, engine_factory
):
    """The same rule for the other half of the retrieval identity."""
    _, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    await _insert_chunk(
        session_maker,
        entity_id=sample_entity.id,
        project_id=test_project.id,
        app_config=app_config,
        vector_index="some-retired-index",
    )

    readiness = await readiness_service.readiness_for(test_project, ())

    assert readiness.stage(ProjectIndexStageName.EMBEDDINGS).pending == 1


@pytest.mark.asyncio
async def test_a_pending_chunk_is_not_settled(
    readiness_service, test_project, sample_entity, app_config, engine_factory
):
    """Retrieval admits only `ready`; so does readiness."""
    _, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    await _insert_chunk(
        session_maker,
        entity_id=sample_entity.id,
        project_id=test_project.id,
        app_config=app_config,
        embedding_status="pending",
    )

    readiness = await readiness_service.readiness_for(test_project, ())

    assert readiness.stage(ProjectIndexStageName.EMBEDDINGS).pending == 1


@pytest.mark.asyncio
async def test_an_embed_false_note_owes_no_embedding_work(
    readiness_service, test_project, app_config, entity_repository, session_maker
):
    """A note that opts out is never owed a vector, so the stage can settle.

    `sync_entity_vectors_batch` clears and skips these. Counting one as owed
    would leave the stage PENDING forever and hang `bm status --wait` on work no
    pass will ever do.
    """
    app_config.semantic_search_enabled = True
    async with db.scoped_session(session_maker) as session:
        await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": "Opted Out",
                "note_type": "test",
                "permalink": "test/opted-out",
                "file_path": "test/opted_out.md",
                "content_type": "text/markdown",
                "entity_metadata": {"embed": "false"},
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
        )

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    assert embeddings.total == 0
    assert embeddings.pending == 0


@pytest.mark.asyncio
async def test_a_missing_vector_manifest_reports_nothing_embedded(
    readiness_service, test_project, sample_entity, app_config, engine_factory
):
    """A database without the vector tables must answer, not raise.

    `bm status` is the one call a waiter polls; taking it down because the
    vector migrations have not run yet would hide the readiness signal exactly
    when a caller needs it.
    """
    _, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    async with db.scoped_session(session_maker) as session:
        await session.execute(text("DROP TABLE IF EXISTS search_vector_chunks"))

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    assert embeddings.total == 1
    assert embeddings.pending == 1


@pytest.mark.asyncio
async def test_a_deferred_entity_is_not_settled(
    readiness_service, test_project, sample_entity, app_config, engine_factory
):
    """One shard of a large note is not the whole note.

    An entity producing more chunks than a shard is processed a shard at a time.
    The chunks that were not scheduled have no manifest row at all, so its
    written rows satisfy the retrieval predicate and it looked fully embedded --
    letting `status --wait` return with the note's later chunks unsearchable.
    """
    _, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    await _insert_chunk(
        session_maker,
        entity_id=sample_entity.id,
        project_id=test_project.id,
        app_config=app_config,
    )
    # What the sharded sync records when it defers the rest of the entity.
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("UPDATE entity SET vector_sync_deferred_at = :now WHERE id = :id"),
            {"now": datetime.now(UTC), "id": sample_entity.id},
        )

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    assert embeddings.total == 1
    assert embeddings.pending == 1
    assert readiness.phase is not ProjectIndexPhase.IDLE


@pytest.mark.asyncio
async def test_clearing_the_deferral_settles_the_entity(
    readiness_service, test_project, sample_entity, app_config, engine_factory
):
    """The marker is cleared when a later pass finishes the entity."""
    _, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    await _insert_chunk(
        session_maker,
        entity_id=sample_entity.id,
        project_id=test_project.id,
        app_config=app_config,
    )
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("UPDATE entity SET vector_sync_deferred_at = NULL WHERE id = :id"),
            {"id": sample_entity.id},
        )

    readiness = await readiness_service.readiness_for(test_project, ())

    assert readiness.stage(ProjectIndexStageName.EMBEDDINGS).pending == 0


@pytest.mark.asyncio
async def test_the_sharded_sync_is_the_writer_of_the_deferral_marker(
    test_project, sample_entity, app_config, engine_factory
):
    """Readiness and the sharding rule share one writer, so they cannot drift."""
    _, session_maker = engine_factory
    repository = create_search_repository(
        session_maker, project_id=test_project.id, app_config=app_config
    )

    await repository.record_entity_vector_deferrals(
        deferred_entity_ids={sample_entity.id}, completed_entity_ids=set()
    )
    async with db.scoped_session(session_maker) as session:
        deferred_at = (
            await session.execute(
                text("SELECT vector_sync_deferred_at FROM entity WHERE id = :id"),
                {"id": sample_entity.id},
            )
        ).scalar()
    assert deferred_at is not None

    await repository.record_entity_vector_deferrals(
        deferred_entity_ids=set(), completed_entity_ids={sample_entity.id}
    )
    async with db.scoped_session(session_maker) as session:
        cleared = (
            await session.execute(
                text("SELECT vector_sync_deferred_at FROM entity WHERE id = :id"),
                {"id": sample_entity.id},
            )
        ).scalar()
    assert cleared is None


# --- Bind-parameter bounds ---

# Above asyncpg's 32767 parameter cap, so this is a genuinely over-limit list on
# Postgres rather than a mocked limit. SQLite builds vary (this one allows
# 250000), which is why the structural assertion below covers both backends.
OVER_LIMIT_ENTITY_COUNT = 40_000


@pytest.mark.asyncio
async def test_deferral_markers_survive_an_over_limit_entity_list(
    test_project, app_config, engine_factory
):
    """`reindex_vectors` hands every entity in the project to one batch.

    Built as one `IN (...)`, that is one bind per entity, and the statement
    raises past the driver's cap -- after the embedding work has already
    succeeded, so `bm reindex` fails and the markers roll back. The ids need not
    exist; the parameter count is what breaks.
    """
    _, session_maker = engine_factory
    repository = create_search_repository(
        session_maker, project_id=test_project.id, app_config=app_config
    )

    await repository.record_entity_vector_deferrals(
        deferred_entity_ids=set(range(1, OVER_LIMIT_ENTITY_COUNT + 1)),
        completed_entity_ids=set(),
    )


@pytest.mark.asyncio
async def test_no_marker_statement_exceeds_the_hydration_bound(
    test_project, app_config, engine_factory, monkeypatch
):
    """Each statement stays within the bound the vector path already uses.

    Asserted structurally because the cap is per driver: a list that is fatal on
    asyncpg is comfortable on some SQLite builds, so counting binds is what makes
    this regression provable on either backend.
    """
    _, session_maker = engine_factory
    repository = create_search_repository(
        session_maker, project_id=test_project.id, app_config=app_config
    )

    bind_counts: list[int] = []
    real_execute = AsyncSession.execute

    async def counting_execute(self, statement, params=None, *args, **kwargs):
        if isinstance(params, dict) and "deferred_at" in params:
            bind_counts.append(len(params))
        return await real_execute(self, statement, params, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", counting_execute)

    await repository.record_entity_vector_deferrals(
        deferred_entity_ids=set(range(1, 601)),
        completed_entity_ids=set(range(601, 1001)),
    )

    assert bind_counts, "no marker statements were executed"
    # Each statement also carries :project_id and :deferred_at.
    assert max(bind_counts) <= VECTOR_HYDRATION_BATCH_SIZE + 2, bind_counts
    # 600 deferred + 400 completed at 250 per statement.
    assert len(bind_counts) == 3 + 2


@pytest.mark.asyncio
async def test_marker_updates_are_one_transaction(
    test_project, sample_entity, app_config, engine_factory, monkeypatch
):
    """A failure part-way through must not leave some entities marked.

    The markers are derived state, so losing the whole update is safe -- the next
    sync pass rewrites them. A half-applied update is not: readiness would carry
    a mix of stale and current markers with nothing to reconcile them.
    """
    _, session_maker = engine_factory
    repository = create_search_repository(
        session_maker, project_id=test_project.id, app_config=app_config
    )

    real_execute = AsyncSession.execute
    seen: list[int] = []

    async def failing_execute(self, statement, params=None, *args, **kwargs):
        if isinstance(params, dict) and "deferred_at" in params:
            seen.append(1)
            if len(seen) == 2:
                raise RuntimeError("driver gave up mid-update")
        return await real_execute(self, statement, params, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", failing_execute)

    with pytest.raises(RuntimeError, match="gave up"):
        await repository.record_entity_vector_deferrals(
            deferred_entity_ids=set(range(1, 601)),
            completed_entity_ids=set(),
        )

    monkeypatch.undo()
    async with db.scoped_session(session_maker) as session:
        marked = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM entity "
                    "WHERE project_id = :project_id AND vector_sync_deferred_at IS NOT NULL"
                ),
                {"project_id": test_project.id},
            )
        ).scalar()

    assert marked == 0


# --- Drainability: nothing is counted that no pass will act on ---


async def _create_entity(entity_repository, session_maker, *, title: str, slug: str) -> Any:
    async with db.scoped_session(session_maker) as session:
        return await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": title,
                "note_type": "test",
                "permalink": f"test/{slug}",
                "file_path": f"test/{slug}.md",
                "content_type": "text/markdown",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
        )


async def _create_unresolved_relation(
    session_maker, *, project_id: int, from_id: int, to_name: str
) -> None:
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO relation (project_id, from_id, to_id, to_name, relation_type) "
                "VALUES (:project_id, :from_id, NULL, :to_name, 'relates_to')"
            ),
            {"project_id": project_id, "from_id": from_id, "to_name": to_name},
        )


@pytest.mark.asyncio
async def test_an_ambiguous_title_link_is_not_counted_as_pending(
    readiness_service, test_project, entity_repository, session_maker
):
    """A link no pass will ever wire up is not pending work.

    `BulkLinkResolver.resolve_strict` refuses a title matching more than one
    note, so nothing drains this count. Counting it left the project PENDING
    forever and `status --wait` timing out on work that would never happen --
    the same shape as counting an `embed: false` note as owed.
    """
    source = await _create_entity(entity_repository, session_maker, title="Source", slug="source")
    # Two notes share a title, so the title is ambiguous and unresolvable.
    await _create_entity(entity_repository, session_maker, title="Twin", slug="twin-one")
    await _create_entity(entity_repository, session_maker, title="Twin", slug="twin-two")
    await _create_unresolved_relation(
        session_maker, project_id=test_project.id, from_id=source.id, to_name="Twin"
    )

    readiness = await readiness_service.readiness_for(test_project, ())

    assert readiness.stage(ProjectIndexStageName.RELATIONS).pending == 0


@pytest.mark.asyncio
async def test_an_unambiguous_title_link_is_still_counted_as_pending(
    readiness_service, test_project, entity_repository, session_maker
):
    """The exclusion must not swallow the case the feature exists for."""
    source = await _create_entity(entity_repository, session_maker, title="Source", slug="source")
    await _create_entity(entity_repository, session_maker, title="Only One", slug="only-one")
    await _create_unresolved_relation(
        session_maker, project_id=test_project.id, from_id=source.id, to_name="Only One"
    )

    readiness = await readiness_service.readiness_for(test_project, ())

    assert readiness.stage(ProjectIndexStageName.RELATIONS).pending == 1


@pytest.mark.asyncio
async def test_a_permalink_link_is_counted_even_when_titles_collide(
    readiness_service, test_project, entity_repository, session_maker
):
    """Permalinks are unique, so a permalink match is never ambiguous."""
    source = await _create_entity(entity_repository, session_maker, title="Source", slug="source")
    await _create_entity(entity_repository, session_maker, title="Twin", slug="twin-one")
    await _create_entity(entity_repository, session_maker, title="Twin", slug="twin-two")
    await _create_unresolved_relation(
        session_maker, project_id=test_project.id, from_id=source.id, to_name="test/twin-one"
    )

    readiness = await readiness_service.readiness_for(test_project, ())

    assert readiness.stage(ProjectIndexStageName.RELATIONS).pending == 1


# --- The drainability matrix ---
#
# One property, stated once and checked over every shape these reviews found:
#
#   Something counted as pending must be drainable by a pass, and something no
#   pass will act on must not be counted.
#
# Both halves have failed in this PR. Stale-identity chunks and a deferred entity
# were *not* counted though a pass would fix them, so readiness said IDLE while
# retrieval returned nothing. An `embed: false` note and an ambiguous-title link
# *were* counted though nothing would ever act on them, so the project could not
# leave PENDING and `status --wait` timed out. A new shape needs a row here.

DRAINS = "a pass will act on it"
NEVER_DRAINS = "no pass will ever act on it"


async def _seed_relation_shape(shape: str, entity_repository, session_maker, project_id: int):
    source = await _create_entity(entity_repository, session_maker, title="Source", slug="source")
    if shape == "dangling":
        target_name = "Nothing Named This"
    elif shape == "ambiguous_title":
        await _create_entity(entity_repository, session_maker, title="Twin", slug="twin-one")
        await _create_entity(entity_repository, session_maker, title="Twin", slug="twin-two")
        target_name = "Twin"
    elif shape == "unique_title":
        await _create_entity(entity_repository, session_maker, title="Only One", slug="only-one")
        target_name = "Only One"
    else:
        assert shape == "permalink"
        await _create_entity(entity_repository, session_maker, title="Target", slug="target")
        target_name = "test/target"
    await _create_unresolved_relation(
        session_maker, project_id=project_id, from_id=source.id, to_name=target_name
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "drainability"),
    [
        ("dangling", NEVER_DRAINS),
        ("ambiguous_title", NEVER_DRAINS),
        ("unique_title", DRAINS),
        ("permalink", DRAINS),
    ],
)
async def test_relations_counted_pending_are_exactly_the_drainable_ones(
    shape, drainability, readiness_service, test_project, entity_repository, session_maker
):
    await _seed_relation_shape(shape, entity_repository, session_maker, test_project.id)

    readiness = await readiness_service.readiness_for(test_project, ())
    pending = readiness.stage(ProjectIndexStageName.RELATIONS).pending

    if drainability is DRAINS:
        assert pending == 1, f"{shape}: a pass would resolve this and it was not counted"
    else:
        assert pending == 0, f"{shape}: nothing drains this, so a waiter would never finish"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "drainability"),
    [
        ("no_chunks", DRAINS),
        ("stale_model", DRAINS),
        ("pending_status", DRAINS),
        ("deferred", DRAINS),
        ("current", NEVER_DRAINS),
        ("embed_false", NEVER_DRAINS),
    ],
)
async def test_embeddings_counted_pending_are_exactly_the_drainable_ones(
    shape,
    drainability,
    readiness_service,
    test_project,
    sample_entity,
    app_config,
    engine_factory,
    session_maker,
):
    """`current` and `embed_false` are "never drains" in the sense that matters here.

    Neither has outstanding work: one is finished, the other opted out. Counting
    either would park the stage in PENDING with nothing able to clear it.
    """
    _, maker = engine_factory
    app_config.semantic_search_enabled = True

    if shape in {"stale_model", "pending_status", "deferred", "current"}:
        await _insert_chunk(
            maker,
            entity_id=sample_entity.id,
            project_id=test_project.id,
            app_config=app_config,
            embedding_model="RetiredProvider:old:384" if shape == "stale_model" else None,
            embedding_status="pending" if shape == "pending_status" else "ready",
        )
    if shape == "deferred":
        async with db.scoped_session(maker) as session:
            await session.execute(
                text("UPDATE entity SET vector_sync_deferred_at = :now WHERE id = :id"),
                {"now": datetime.now(UTC), "id": sample_entity.id},
            )
    if shape == "embed_false":
        async with db.scoped_session(maker) as session:
            await session.execute(
                text("UPDATE entity SET entity_metadata = :meta WHERE id = :id"),
                {"meta": '{"embed": "false"}', "id": sample_entity.id},
            )

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    if drainability is DRAINS:
        assert embeddings.pending == 1, (
            f"{shape}: a sync pass would fix this and it was not counted"
        )
    else:
        assert embeddings.pending == 0, (
            f"{shape}: nothing drains this, so a waiter would never finish"
        )
