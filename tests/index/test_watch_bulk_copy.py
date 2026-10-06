"""Files copied in bulk into a running server must all be indexed, without a restart.

Whole folders of notes copied in this way stayed on disk and out of the index,
with nothing logged. Two causes, both covered here:

* On Linux a new directory is watched only after it appears. If it cannot be
  read at that instant (created closed and then handed to another owner, which
  is what `install -d -o <user>` run as root does), the watch is never added and
  no file written into it is ever reported. The end-to-end tests at the bottom
  reproduce that with a real watcher and real indexing.
* The watch loop restarted itself on every project-reload tick, and a restart
  discards the changes collected but not yet handed over: during a slow batch,
  everything written in the meantime.

The restart tests drive the real watch loop (`WatchService.run`, real
watchfiles, a real directory) with the indexing step replaced by one that
records what it was handed and is slow on its first batch.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import override

import pytest
from loguru import logger
from watchfiles import Change

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
from basic_memory.index.watch_reconcile import expand_new_directories
from basic_memory.index.watch_service import WatchService
from basic_memory.models import Project

#: How long the copy may take to be fully indexed. The scenario needs about four
#: seconds; the rest is room for a slow CI filesystem watcher.
CONVERGENCE_CEILING_SECONDS = 20.0

#: How long the first batch takes to "index", in seconds. Longer than the reload
#: interval, so a reload tick always lands while the copy's second half waits.
SLOW_BATCH_SECONDS = 3.0


class RecordingWatchService(WatchService):
    """The real watch loop, with indexing replaced by a slow recorder."""

    def __init__(self, *args, project: Project, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.project = project
        self.seen: set[str] = set()
        self.first_batch_started = asyncio.Event()
        self.batches = 0
        self.cycles = 0
        # This file tests the watcher; the reconcile would hide what it does.
        self.reconcile_enabled = False

    @override
    async def _select_projects_to_watch(self) -> list[Project]:
        return [self.project]

    @override
    async def _watch_projects_cycle(self, projects, stop_event) -> None:
        self.cycles += 1
        await super()._watch_projects_cycle(projects, stop_event)

    @override
    async def handle_changes(self, project, changes) -> None:  # type: ignore[override]
        self.batches += 1
        first = self.batches == 1
        # As the real handler does: a reported new directory stands for its files.
        # On Linux the first note can land before the new folder's watch exists.
        changes, _new = expand_new_directories(
            changes, project_root=Path(project.path), ignore_patterns=set()
        )
        self.seen.update(Path(path).name for _change, path in changes if path.endswith(".md"))
        if first:
            self.first_batch_started.set()
            await asyncio.sleep(SLOW_BATCH_SECONDS)

    @override
    async def write_status(self) -> None:
        return None


class RestartEveryTickWatchService(RecordingWatchService):
    """The negative control: the old behavior, a restart on every reload tick.

    Only from the first batch on. A restart before then could swallow the first
    write as well, and the control would fail for a reason that is not the defect.
    """

    @override
    def _project_set_changed(self, watched, current: Sequence[Project]) -> bool:
        return self.first_batch_started.is_set()


def _watch_config(app_config: BasicMemoryConfig) -> BasicMemoryConfig:
    return app_config.model_copy(update={"watch_project_reload_interval": 1, "index_delay": 200})


async def _bulk_copy_into_busy_watcher(
    service: RecordingWatchService, vault: Path
) -> tuple[set[str], float | None]:
    """Copy two folders in while the first batch is still indexing.

    Returns the note names the watcher delivered, and how long after the copy
    finished the last one arrived (None if they never all did).
    """
    expected = {"Charter.md"} | {f"Person {i}.md" for i in range(12)}
    run_task = asyncio.create_task(service.run())
    try:
        # Let the watcher start. Writing before it does would test nothing.
        await asyncio.sleep(1.0)
        (vault / "Charter.md").write_text("# Charter\n")
        try:
            await asyncio.wait_for(service.first_batch_started.wait(), timeout=10)
        except TimeoutError:
            # Even the first write was lost (a restart between it and its delivery).
            return service.seen, None

        # The first batch is now "indexing" for SLOW_BATCH_SECONDS. The rest of the
        # copy lands in a folder the copy creates, as a bulk copy does.
        people = vault / "people"
        people.mkdir()
        for i in range(12):
            (people / f"Person {i}.md").write_text(f"# Person {i}\n")
        copied_at = time.monotonic()

        deadline = copied_at + CONVERGENCE_CEILING_SECONDS
        while time.monotonic() < deadline:
            if expected <= service.seen:
                return service.seen, time.monotonic() - copied_at
            await asyncio.sleep(0.1)
        return service.seen, None
    finally:
        service.state.running = False
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


def _project(vault: Path) -> Project:
    return Project(id=1, name="vault", permalink="vault", path=str(vault))


@pytest.mark.asyncio
async def test_bulk_copy_into_a_busy_watcher_is_fully_delivered(
    app_config: BasicMemoryConfig, project_repository, session_maker, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    service = RecordingWatchService(
        app_config=_watch_config(app_config),
        project_repository=project_repository,
        session_maker=session_maker,
        project=_project(vault),
    )

    seen, converged_after = await _bulk_copy_into_busy_watcher(service, vault)

    missing = sorted(({"Charter.md"} | {f"Person {i}.md" for i in range(12)}) - seen)
    assert converged_after is not None, f"never delivered: {missing}"
    assert converged_after < CONVERGENCE_CEILING_SECONDS
    # Reload ticks came and went during the slow batch without a restart.
    assert service.cycles == 1


@pytest.mark.asyncio
async def test_negative_control_restart_on_every_tick_loses_the_copy(
    app_config: BasicMemoryConfig, project_repository, session_maker, tmp_path: Path
) -> None:
    """The harness can see the loss: with the old restart, the second folder never arrives.

    If this test starts failing, the scenario above no longer exercises the
    defect, and its pass means nothing.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    service = RestartEveryTickWatchService(
        app_config=_watch_config(app_config),
        project_repository=project_repository,
        session_maker=session_maker,
        project=_project(vault),
    )

    seen, converged_after = await _bulk_copy_into_busy_watcher(service, vault)

    assert converged_after is None
    assert not ({f"Person {i}.md" for i in range(12)} & seen)
    assert service.cycles >= 2


# --- the reconcile -----------------------------------------------------------------


def _reconcile_service(app_config, project_repository, session_maker) -> WatchService:
    service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )
    service.reconcile_settle_seconds = 0
    return service


async def _indexed_paths(session_maker, entity_repository) -> set[str]:
    async with db.scoped_session(session_maker) as session:
        return {entity.file_path for entity in await entity_repository.find_all(session)}


@pytest.mark.asyncio
async def test_reconcile_indexes_a_file_the_watcher_never_reported(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
    entity_repository,
) -> None:
    """A note on disk with no watcher event behind it is found, indexed, and logged."""
    root = Path(project_config.home)
    (root / "people").mkdir(parents=True, exist_ok=True)
    (root / "people" / "Missed.md").write_text("# Missed\n\nnever reported\n")
    service = _reconcile_service(app_config, project_repository, session_maker)
    assert "people/Missed.md" not in await _indexed_paths(session_maker, entity_repository)

    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        reconciled = await service.reconcile_unindexed_files([test_project])
    finally:
        logger.remove(sink)

    assert reconciled == 1
    assert "people/Missed.md" in await _indexed_paths(session_maker, entity_repository)
    assert any("Index reconcile" in line and "watcher missed" in line for line in warnings)

    # A second pass finds nothing to do and says nothing.
    warnings.clear()
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        assert await service.reconcile_unindexed_files([test_project]) == 0
    finally:
        logger.remove(sink)
    assert not any("Index reconcile" in line for line in warnings)


@pytest.mark.asyncio
async def test_reconcile_leaves_a_file_still_settling_to_the_watcher(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
    entity_repository,
) -> None:
    """A file written moments ago may still be in the watcher's debounce: hands off."""
    root = Path(project_config.home)
    (root / "Fresh.md").write_text("# Fresh\n")
    service = _reconcile_service(app_config, project_repository, session_maker)
    service.reconcile_settle_seconds = 3600

    assert await service.reconcile_unindexed_files([test_project]) == 0
    assert "Fresh.md" not in await _indexed_paths(session_maker, entity_repository)


@pytest.mark.asyncio
async def test_reconcile_does_not_retry_a_file_that_will_not_index(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
) -> None:
    """A file indexing cannot take is tried once, not on every tick until it changes."""
    root = Path(project_config.home)
    (root / "Stubborn.md").write_text("# Stubborn\n")
    handed: list[set[str]] = []

    class NeverIndexes(WatchService):
        @override
        async def handle_changes(self, project, changes) -> None:  # type: ignore[override]
            handed.append({Path(path).name for _change, path in changes})

    service = NeverIndexes(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )
    service.reconcile_settle_seconds = 0

    assert await service.reconcile_unindexed_files([test_project]) == 1
    assert await service.reconcile_unindexed_files([test_project]) == 0
    assert handed == [{"Stubborn.md"}]

    # Once it changes, it is worth another try.
    (root / "Stubborn.md").write_text("# Stubborn\n\nedited, longer\n")
    assert await service.reconcile_unindexed_files([test_project]) == 1


@pytest.mark.asyncio
async def test_reconcile_waits_while_indexing_is_busy(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
) -> None:
    """Neither a batch in flight nor the startup scan shares the field with a reconcile."""
    calls: list[int] = []

    class Counting(WatchService):
        @override
        async def reconcile_unindexed_files(self, projects) -> int:
            calls.append(1)
            return 0

    pending = True
    service = Counting(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        initial_index_pending=lambda: pending,
    )

    await service._reconcile_when_idle([test_project])
    assert calls == []

    pending = False
    service._batches_in_flight = 1
    await service._reconcile_when_idle([test_project])
    assert calls == []

    service._batches_in_flight = 0
    await service._reconcile_when_idle([test_project])
    assert calls == [1]


# --- a new directory whose files the watcher never reports --------------------------


@pytest.mark.asyncio
async def test_a_reported_new_directory_indexes_the_files_inside_it(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
    entity_repository,
) -> None:
    """The batch names only the directory, as it does when its own watch failed."""
    root = Path(project_config.home)
    people = root / "people"
    (people / "nested").mkdir(parents=True)
    for name in ("Ann.md", "Bo.md", "nested/Cy.md"):
        (people / name).write_text(f"# {Path(name).stem}\n")
    (people / ".hidden.md").write_text("# hidden\n")
    service = _reconcile_service(app_config, project_repository, session_maker)

    await service.handle_changes(test_project, {(Change.added, str(people))})

    indexed = await _indexed_paths(session_maker, entity_repository)
    assert {"people/Ann.md", "people/Bo.md", "people/nested/Cy.md"} <= indexed
    assert "people/.hidden.md" not in indexed
    assert service._new_directories_seen is True


@pytest.mark.asyncio
async def test_a_directory_moved_inside_the_project_moves_its_rows(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
    entity_repository,
) -> None:
    """A renamed folder arrives as one deleted and one added directory: rows move, none double."""
    root = Path(project_config.home)
    (root / "old").mkdir()
    (root / "old" / "Ann.md").write_text("# Ann\n")
    (root / "old" / "Bo.md").write_text("# Bo\n")
    service = _reconcile_service(app_config, project_repository, session_maker)
    await service.handle_changes(
        test_project,
        {(Change.added, str(root / "old" / "Ann.md")), (Change.added, str(root / "old" / "Bo.md"))},
    )
    async with db.scoped_session(session_maker) as session:
        before = {e.file_path: e.id for e in await entity_repository.find_all(session)}
    assert {"old/Ann.md", "old/Bo.md"} <= set(before)

    (root / "old").rename(root / "new")
    await service.handle_changes(
        test_project, {(Change.deleted, str(root / "old")), (Change.added, str(root / "new"))}
    )

    async with db.scoped_session(session_maker) as session:
        after = {e.file_path: e.id for e in await entity_repository.find_all(session)}
    assert not {"old/Ann.md", "old/Bo.md"} & set(after)
    assert after["new/Ann.md"] == before["old/Ann.md"]
    assert after["new/Bo.md"] == before["old/Bo.md"]


# --- end to end on Linux: the watch on a new directory fails ------------------------


LINUX_NON_ROOT = sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() != 0


async def _copy_with_a_closed_directory(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
    entity_repository,
) -> tuple[set[str], float | None]:
    """Run the real watcher; create `people/` closed, then open it and copy notes in.

    A directory that cannot be read when the watcher learns of it is the shape
    `install -d -o 1000 -g 1000` run as root produces for an instant. Here it is
    held closed for a beat so the outcome does not depend on scheduling. Returns
    what got indexed and how long after the copy the index matched the disk.
    """
    root = Path(project_config.home)
    config = app_config.model_copy(update={"watch_project_reload_interval": 1, "index_delay": 200})
    service = WatchService(
        app_config=config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )
    service.reconcile_settle_seconds = 0
    expected = {f"people/Person {i}.md" for i in range(8)}
    run_task = asyncio.create_task(service.run())
    try:
        await asyncio.sleep(1.5)
        people = root / "people"
        people.mkdir(mode=0o000)
        await asyncio.sleep(1.0)
        people.chmod(0o755)
        for i in range(8):
            (people / f"Person {i}.md").write_text(f"# Person {i}\n")
            await asyncio.sleep(0.1)
        copied_at = time.monotonic()
        deadline = copied_at + E2E_CEILING_SECONDS
        indexed: set[str] = set()
        while time.monotonic() < deadline:
            indexed = await _indexed_paths(session_maker, entity_repository)
            if expected <= indexed:
                return indexed, time.monotonic() - copied_at
            await asyncio.sleep(0.25)
        return indexed, None
    finally:
        service.state.running = False
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


#: The new directory is walked in the batch that reports it (about a second), and
#: anything written after is indexed by the reconcile after the restart (reload
#: tick, then the quiet period of 3 x index_delay + 5 s). Twenty seconds is double that.
E2E_CEILING_SECONDS = 20.0


@pytest.mark.skipif(not LINUX_NON_ROOT, reason="needs inotify, and a user a closed directory stops")
@pytest.mark.asyncio
async def test_a_copy_into_a_directory_the_watcher_cannot_watch_is_indexed(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
    entity_repository,
) -> None:
    indexed, converged_after = await _copy_with_a_closed_directory(
        app_config,
        project_repository,
        session_maker,
        test_project,
        project_config,
        entity_repository,
    )
    missing = sorted({f"people/Person {i}.md" for i in range(8)} - indexed)
    assert converged_after is not None, f"never indexed: {missing}"
    assert converged_after < E2E_CEILING_SECONDS


@pytest.mark.skipif(not LINUX_NON_ROOT, reason="needs inotify, and a user a closed directory stops")
@pytest.mark.asyncio
async def test_negative_control_without_the_fix_the_copy_stays_unindexed(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
    entity_repository,
    monkeypatch,
) -> None:
    """With directory expansion and the reconcile off, the same copy is never indexed.

    This is the defect as shipped: proof that the scenario above reproduces it.
    """
    from basic_memory.index import watch_service as watch_service_module

    monkeypatch.setattr(
        watch_service_module, "expand_new_directories", lambda changes, **_: (changes, ())
    )
    monkeypatch.setattr(WatchService, "reconcile_unindexed_files", _reconcile_nothing)

    indexed, converged_after = await _copy_with_a_closed_directory(
        app_config,
        project_repository,
        session_maker,
        test_project,
        project_config,
        entity_repository,
    )
    assert converged_after is None
    assert not {f"people/Person {i}.md" for i in range(8)} & indexed


async def _reconcile_nothing(self, projects) -> int:
    return 0
