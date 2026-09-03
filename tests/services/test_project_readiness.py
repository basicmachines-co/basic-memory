"""Unit coverage for the project readiness contract (#1414).

The end-to-end proof lives in `tests/cli/test_project_add_indexing.py`. These
pin the parts of the contract a CLI run cannot reach cheaply: the phase algebra,
the honest state wording, and the per-stage counts against a real database.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.models import Project
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


@pytest.mark.asyncio
async def test_a_ready_chunk_settles_the_embedding_stage(
    readiness_service, test_project, sample_entity, app_config, engine_factory
):
    _, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks "
                "(entity_id, project_id, chunk_key, chunk_text, source_hash, "
                " entity_fingerprint, embedding_model, vector_index, embedding_status) "
                "VALUES (:entity_id, :project_id, 'k', 't', 'h', 'f', 'm', 'i', 'ready')"
            ),
            {"entity_id": sample_entity.id, "project_id": test_project.id},
        )

    readiness = await readiness_service.readiness_for(test_project, ())
    embeddings = readiness.stage(ProjectIndexStageName.EMBEDDINGS)

    assert embeddings.total == 1
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
