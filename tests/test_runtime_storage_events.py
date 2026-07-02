"""Tests for portable storage-event helpers."""

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
