"""Tests for portable storage-event helpers."""

import asyncio
from typing import override

import pytest

from basic_memory.runtime.storage import (
    RuntimeStorageEventOperation,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
    group_storage_events_by_bucket,
)
from basic_memory.runtime.storage_events import (
    RuntimeStorageEventSource,
    StorageEventInput,
    run_runtime_storage_event_operations,
    storage_event_payload_from_input,
)


def storage_event(
    *,
    bucket_name: str,
    key: str,
    event_name: str = "OBJECT_CREATED_PUT",
    etag: str = "etag",
) -> StorageEventPayload:
    return StorageEventPayload(
        event_name=event_name,
        event_time="2026-06-19T10:15:00Z",
        object_version=StorageObjectVersion(
            identity=StorageObjectIdentity(bucket_name=bucket_name, key=key),
            etag=etag,
            size=10,
        ),
    )


def test_group_storage_events_by_bucket_preserves_bucket_and_arrival_order() -> None:
    first = storage_event(bucket_name="alpha", key="main/a.md", etag="a")
    second = storage_event(bucket_name="beta", key="main/b.md", etag="b")
    third = storage_event(bucket_name="alpha", key="main/c.md", etag="c")

    grouped = group_storage_events_by_bucket((first, second, third))

    assert grouped == {
        "alpha": (first, third),
        "beta": (second,),
    }


def test_storage_event_payload_from_input_builds_runtime_payload() -> None:
    payload = storage_event_payload_from_input(
        StorageEventInput(
            event_name="OBJECT_CREATED_POST",
            event_time="2026-06-19T10:15:00Z",
            bucket_name="tenant-bucket",
            object_key="main/notes/a.md",
            etag='"etag-a"',
            size=42,
        )
    )

    assert payload.event_name == "OBJECT_CREATED_POST"
    assert payload.event_time == "2026-06-19T10:15:00Z"
    assert payload.bucket_name == "tenant-bucket"
    assert payload.object_key == "main/notes/a.md"
    assert payload.project_path == "main"
    assert payload.relative_path == "notes/a.md"
    assert payload.etag == '"etag-a"'
    assert payload.size == 42


def test_runtime_storage_event_source_groups_inputs_by_bucket() -> None:
    source = RuntimeStorageEventSource.from_inputs(
        (
            StorageEventInput(
                event_name="OBJECT_CREATED_PUT",
                event_time="2026-06-19T10:15:00Z",
                bucket_name="alpha",
                object_key="main/a.md",
                etag="a",
                size=1,
            ),
            StorageEventInput(
                event_name="OBJECT_CREATED_POST",
                event_time="2026-06-19T10:16:00Z",
                bucket_name="beta",
                object_key="main/b.md",
                etag="b",
                size=2,
            ),
            StorageEventInput(
                event_name="OBJECT_DELETED",
                event_time="2026-06-19T10:17:00Z",
                bucket_name="alpha",
                object_key="main/c.md",
                etag="c",
                size=3,
            ),
        )
    )

    grouped = source.events_by_bucket()

    assert list(grouped) == ["alpha", "beta"]
    assert [event.object_key for event in grouped["alpha"]] == ["main/a.md", "main/c.md"]
    assert grouped["beta"][0].etag == "b"


class RecordingStorageEventProcessor:
    """Fake storage-event processor for portable runner tests."""

    def __init__(self, fail_relative_path: str | None = None) -> None:
        self.fail_relative_path = fail_relative_path
        self.calls: list[tuple[str, str, str]] = []

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        skip_reason = operation.skip_reason
        if skip_reason is None:
            raise AssertionError("skip operation missing reason")
        self.calls.append(
            (
                "skip",
                skip_reason.value,
                operation.relative_path or "",
            )
        )

    async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
        relative_path = operation.require_relative_path()
        self.calls.append(("index", "", relative_path))
        if relative_path == self.fail_relative_path:
            raise RuntimeError("index failed")

    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        self.calls.append(("delete", "", operation.require_relative_path()))

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        self.calls.append(("failed", str(exc), operation.relative_path or ""))


@pytest.mark.asyncio
async def test_run_runtime_storage_event_operations_counts_adapter_results() -> None:
    processor = RecordingStorageEventProcessor(fail_relative_path="notes/fail.md")

    result = await run_runtime_storage_event_operations(
        (
            storage_event(bucket_name="alpha", key="main/notes/a.md"),
            storage_event(
                bucket_name="alpha",
                key="main/notes/b.md",
                event_name="OBJECT_DELETED",
            ),
            storage_event(bucket_name="alpha", key="main/image.png"),
            storage_event(
                bucket_name="alpha",
                key="main/notes/c.md",
                event_name="OBJECT_RESTORED",
            ),
            storage_event(bucket_name="alpha", key="main/notes/fail.md"),
        ),
        processor,
    )

    assert result.as_dict() == {"processed": 3, "failed": 1, "skipped": 1}
    assert processor.calls == [
        ("index", "", "notes/a.md"),
        ("delete", "", "notes/b.md"),
        ("index", "", "image.png"),
        ("skip", "unknown_event", "notes/c.md"),
        ("index", "", "notes/fail.md"),
        ("failed", "index failed", "notes/fail.md"),
    ]


def delete_event(key: str) -> StorageEventPayload:
    return storage_event(bucket_name="alpha", key=key, event_name="OBJECT_DELETED")


@pytest.mark.asyncio
async def test_repeated_deletes_of_one_path_run_one_check() -> None:
    """A version prune sends one delete per removed version; one check answers them."""
    processor = RecordingStorageEventProcessor()

    result = await run_runtime_storage_event_operations(
        (
            delete_event("main/notes/a.md"),
            delete_event("main/notes/a.md"),
            delete_event("main/notes/b.md"),
            delete_event("main/notes/a.md"),
        ),
        processor,
    )

    assert result.as_dict() == {"processed": 4, "failed": 0, "skipped": 0}
    assert processor.calls == [
        ("delete", "", "notes/a.md"),
        ("delete", "", "notes/b.md"),
    ]


@pytest.mark.asyncio
async def test_a_create_between_deletes_keeps_each_step_in_order() -> None:
    processor = RecordingStorageEventProcessor()

    result = await run_runtime_storage_event_operations(
        (
            delete_event("main/notes/a.md"),
            storage_event(bucket_name="alpha", key="main/notes/a.md"),
            delete_event("main/notes/a.md"),
        ),
        processor,
    )

    assert result.as_dict() == {"processed": 3, "failed": 0, "skipped": 0}
    assert processor.calls == [
        ("delete", "", "notes/a.md"),
        ("index", "", "notes/a.md"),
        ("delete", "", "notes/a.md"),
    ]


class FailingDeleteProcessor(RecordingStorageEventProcessor):
    @override
    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        await super().delete_file(operation)
        raise RuntimeError("delete failed")


@pytest.mark.asyncio
async def test_a_failed_collapsed_check_counts_every_event_it_covers() -> None:
    processor = FailingDeleteProcessor()

    result = await run_runtime_storage_event_operations(
        (delete_event("main/notes/a.md"),) * 3,
        processor,
    )

    assert result.as_dict() == {"processed": 0, "failed": 3, "skipped": 0}
    assert processor.calls == [
        ("delete", "", "notes/a.md"),
        ("failed", "delete failed", "notes/a.md"),
    ]


class ConcurrencyProbe(RecordingStorageEventProcessor):
    """Holds each operation open briefly and records how many overlap, per path too."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.max_in_flight = 0
        self.paths_in_flight: set[str] = set()

    async def _hold(self, operation: RuntimeStorageEventOperation) -> None:
        path = operation.require_relative_path()
        assert path not in self.paths_in_flight, f"{path} ran concurrently with itself"
        self.paths_in_flight.add(path)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        self.paths_in_flight.remove(path)

    @override
    async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
        await self._hold(operation)
        await super().index_file(operation)

    @override
    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        await self._hold(operation)
        await super().delete_file(operation)


def three_paths_with_two_steps_each() -> tuple[StorageEventPayload, ...]:
    events: list[StorageEventPayload] = []
    for name in ("a", "b", "c"):
        events.append(delete_event(f"main/notes/{name}.md"))
        events.append(storage_event(bucket_name="alpha", key=f"main/notes/{name}.md"))
    return tuple(events)


@pytest.mark.asyncio
async def test_different_paths_run_concurrently_up_to_the_limit() -> None:
    processor = ConcurrencyProbe()

    result = await run_runtime_storage_event_operations(
        three_paths_with_two_steps_each(),
        processor,
        max_concurrent_paths=2,
    )

    assert result.as_dict() == {"processed": 6, "failed": 0, "skipped": 0}
    assert processor.max_in_flight == 2
    for name in ("a", "b", "c"):
        path_calls = [call[0] for call in processor.calls if call[2] == f"notes/{name}.md"]
        assert path_calls == ["delete", "index"]


@pytest.mark.asyncio
async def test_paths_run_one_at_a_time_by_default() -> None:
    processor = ConcurrencyProbe()

    await run_runtime_storage_event_operations(three_paths_with_two_steps_each(), processor)

    assert processor.max_in_flight == 1


class RaisingFailureProcessor(FailingDeleteProcessor):
    """A runtime that refuses to count a failure and raises it to the batch instead."""

    @override
    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        raise exc


@pytest.mark.asyncio
async def test_a_raised_failure_fails_the_concurrent_batch_as_itself() -> None:
    with pytest.raises(RuntimeError, match="delete failed"):
        await run_runtime_storage_event_operations(
            (delete_event("main/notes/a.md"), delete_event("main/notes/b.md")),
            RaisingFailureProcessor(),
            max_concurrent_paths=2,
        )


@pytest.mark.asyncio
async def test_max_concurrent_paths_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        await run_runtime_storage_event_operations(
            (), RecordingStorageEventProcessor(), max_concurrent_paths=0
        )
