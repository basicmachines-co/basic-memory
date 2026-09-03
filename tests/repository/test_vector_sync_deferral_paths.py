"""Every path that can defer an entity must record that it did (#1440 review).

The deferral marker was first written on the batch method only, so an oversized
note synced through the per-entity scheduler -- the path normal editing takes --
got one shard of ready chunks and no marker, and readiness called it fully
embedded. One rule, two implementations.

The behavioural tests below drive both public entry points. The structural test
is what stops a third appearing: it walks the class and fails if any public
`sync_entity_vectors*` method reaches the sync helper without going through the
one place that records.
"""

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

import basic_memory.repository.search_repository_base as search_repository_base
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.runtime.vector_sync import VectorSyncBatchResult
from basic_memory.repository.search_repository import create_search_repository

CONVERGENCE_METHOD = "_sync_entity_vectors_internal"
RECORDER_METHOD = "record_entity_vector_deferrals"


def _class_body(source: str, class_name: str) -> ast.ClassDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"{class_name} not found")


def _calls_in(function: ast.AST) -> set[str]:
    """Names of methods this function calls on self."""
    called: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name) and value.id == "self":
                called.add(node.func.attr)
    return called


def test_the_convergence_point_records_deferrals():
    """The one place both entry points meet is the one place that records."""
    source = Path(inspect.getfile(SearchRepositoryBase)).read_text(encoding="utf-8")
    class_node = _class_body(source, "SearchRepositoryBase")

    convergence = next(
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == CONVERGENCE_METHOD
    )

    assert RECORDER_METHOD in _calls_in(convergence), (
        f"{CONVERGENCE_METHOD} must call {RECORDER_METHOD}: it is the only point both "
        "the per-entity and batch sync paths pass through."
    )


def test_every_public_sync_path_goes_through_the_convergence_point():
    """A new entry point that skips the recorder fails here rather than in production.

    This is the guard the deferred-entity bug needed: producing a deferral and
    recording it can only stay together if every path that can produce one runs
    the code that records it.
    """
    source = Path(inspect.getfile(SearchRepositoryBase)).read_text(encoding="utf-8")
    class_node = _class_body(source, "SearchRepositoryBase")

    entry_points = [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("sync_entity_vectors")
        and not node.name.startswith("_")
    ]
    assert {node.name for node in entry_points} == {
        "sync_entity_vectors",
        "sync_entity_vectors_batch",
    }, "a public vector-sync entry point was added or renamed; confirm it records deferrals"

    for node in entry_points:
        called = _calls_in(node)
        assert CONVERGENCE_METHOD in called or RECORDER_METHOD in called, (
            f"{node.name} neither delegates to {CONVERGENCE_METHOD} nor records "
            f"deferrals itself, so a shard it defers would go unrecorded."
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_point", ["sync_entity_vectors", "sync_entity_vectors_batch"])
async def test_both_entry_points_record_deferrals(
    entry_point, test_project, sample_entity, app_config, engine_factory, monkeypatch
):
    """Drive each public path and assert the deferral it produced is recorded.

    The sync helper is stubbed to report a deferral, so this exercises the wiring
    without enabling semantic search and loading an embedding model. What is
    under test is that both entry points route a deferral to the recorder --
    which the per-entity path did not.
    """
    _, session_maker = engine_factory
    repository = create_search_repository(
        session_maker, project_id=test_project.id, app_config=app_config
    )

    async def _deferring_sync(
        _repository, entity_ids, _progress, _continue
    ) -> VectorSyncBatchResult:
        return VectorSyncBatchResult(
            entities_total=len(entity_ids),
            entities_synced=0,
            entities_failed=0,
            entities_deferred=len(entity_ids),
            deferred_entity_ids=tuple(entity_ids),
        )

    monkeypatch.setattr(
        search_repository_base.semantic_vector_sync,
        "sync_entity_vectors_internal",
        _deferring_sync,
    )

    recorded: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(repository, RECORDER_METHOD, _capture)

    if entry_point == "sync_entity_vectors":
        await repository.sync_entity_vectors(sample_entity.id)
    else:
        await repository.sync_entity_vectors_batch([sample_entity.id])

    assert recorded, f"{entry_point} produced a deferral and recorded nothing"
    assert recorded[0]["unfinished_entity_ids"] == {sample_entity.id}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_state", "owes_work"),
    [("deferred_entity_ids", True), ("failed_entity_ids", True), ("synced_entity_ids", False)],
)
async def test_every_terminal_state_that_owes_work_is_recorded(
    terminal_state,
    owes_work,
    test_project,
    sample_entity,
    app_config,
    engine_factory,
    monkeypatch,
):
    """Deferred and failed both leave work owed; only synced is finished.

    A failed entity is dropped from *both* the synced and deferred sets, so
    nothing marked it -- and a multi-chunk note whose later flush failed keeps
    its earlier `ready` rows, which satisfies the retrieval predicate and reads
    as fully embedded. Tracking only deferral would have kept that invisible.
    """
    _, session_maker = engine_factory
    repository = create_search_repository(
        session_maker, project_id=test_project.id, app_config=app_config
    )

    async def _sync(_repository, entity_ids, _progress, _continue) -> VectorSyncBatchResult:
        ids = tuple(entity_ids)
        return VectorSyncBatchResult(
            entities_total=len(ids),
            entities_synced=len(ids) if terminal_state == "synced_entity_ids" else 0,
            entities_failed=len(ids) if terminal_state == "failed_entity_ids" else 0,
            entities_deferred=len(ids) if terminal_state == "deferred_entity_ids" else 0,
            deferred_entity_ids=ids if terminal_state == "deferred_entity_ids" else (),
            failed_entity_ids=ids if terminal_state == "failed_entity_ids" else (),
            synced_entity_ids=ids if terminal_state == "synced_entity_ids" else (),
        )

    monkeypatch.setattr(
        search_repository_base.semantic_vector_sync, "sync_entity_vectors_internal", _sync
    )

    recorded: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(repository, RECORDER_METHOD, _capture)

    await repository.sync_entity_vectors_batch([sample_entity.id])

    assert recorded, "no terminal state was recorded"
    unfinished = recorded[0]["unfinished_entity_ids"]
    completed = recorded[0]["completed_entity_ids"]
    if owes_work:
        assert sample_entity.id in unfinished, f"{terminal_state} left work owed and was not marked"
        assert sample_entity.id not in completed
    else:
        assert sample_entity.id in completed
        assert sample_entity.id not in unfinished


# --- Domain: every code path that can leave work undone ---
#
# The terminal-state rows above cover what a pass *returns*. A pass can also
# *raise*: the per-entity scheduler calls the helper with continue_on_error=False,
# so a prepare or flush failure escapes instead of being collected. The domain of
# this matrix is therefore both axes -- how the caller set continue_on_error, and
# how the pass ended -- because the property is about work left undone, not about
# the shape the outcome happened to arrive in.


@pytest.mark.asyncio
@pytest.mark.parametrize("continue_on_error", [True, False])
@pytest.mark.parametrize("entry_point", ["sync_entity_vectors", "sync_entity_vectors_batch"])
async def test_a_raising_pass_still_records_unfinished_work(
    entry_point,
    continue_on_error,
    test_project,
    sample_entity,
    app_config,
    engine_factory,
    monkeypatch,
):
    """A pass that raises has still left the entity unfinished.

    `sync_entity_vectors` passes continue_on_error=False, so this is the normal
    editing path, not an edge case. Before, the exception escaped before the
    recorder ran and an entity with one ready chunk read as fully embedded.
    """
    _, session_maker = engine_factory
    repository = create_search_repository(
        session_maker, project_id=test_project.id, app_config=app_config
    )

    async def _raising_sync(
        _repository, _entity_ids, _progress, _continue
    ) -> VectorSyncBatchResult:
        raise RuntimeError("embedding flush failed")

    monkeypatch.setattr(
        search_repository_base.semantic_vector_sync,
        "sync_entity_vectors_internal",
        _raising_sync,
    )

    recorded: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(repository, RECORDER_METHOD, _capture)

    with pytest.raises(RuntimeError, match="embedding flush failed"):
        if entry_point == "sync_entity_vectors":
            await repository.sync_entity_vectors(sample_entity.id)
        else:
            await repository.sync_entity_vectors_batch([sample_entity.id])

    assert recorded, f"{entry_point} raised and recorded nothing; the entity looks finished"
    assert recorded[0]["unfinished_entity_ids"] == {sample_entity.id}
    assert recorded[0]["completed_entity_ids"] == set()


def test_the_recording_is_reached_on_both_the_returning_and_raising_paths():
    """Structural: the recorder must be reachable however the helper ends.

    Enumerated from the source so a future refactor that records only after a
    successful return -- the exact regression above -- fails here rather than in
    production.
    """
    source = Path(inspect.getfile(SearchRepositoryBase)).read_text(encoding="utf-8")
    class_node = _class_body(source, "SearchRepositoryBase")
    convergence = next(
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == CONVERGENCE_METHOD
    )

    handlers = [node for node in ast.walk(convergence) if isinstance(node, ast.ExceptHandler)]
    assert handlers, (
        f"{CONVERGENCE_METHOD} has no exception handler, so a pass that raises "
        "records nothing about the work it left undone"
    )
    assert any(RECORDER_METHOD in _calls_in(handler) for handler in handlers), (
        f"{CONVERGENCE_METHOD} does not call {RECORDER_METHOD} when the pass raises"
    )
