"""Tests for local watcher event-index orchestration."""

from dataclasses import dataclass, field
from pathlib import Path

from watchfiles import Change

from basic_memory.index import (
    LocalWatchEventIndexRequest,
    StorageEventIndexRuntime,
    StorageEventOperationProcessorFactory,
    StorageEventProjectResolver,
    run_local_watch_event_indexing,
)
from basic_memory.runtime import ProjectRuntimeReference, RuntimeStorageEventOperation


def project_reference(project_path: str) -> ProjectRuntimeReference:
    return ProjectRuntimeReference(
        project_id=11,
        project_external_id="project-local",
        project_path=project_path,
        project_name="Local",
    )


@dataclass(slots=True)
class RecordingProjectResolver(StorageEventProjectResolver):
    project_path: str
    requested_paths: list[str] = field(default_factory=list)

    async def resolve_project(self, project_path: str) -> ProjectRuntimeReference | None:
        self.requested_paths.append(project_path)
        if project_path != self.project_path:
            return None
        return project_reference(project_path)


@dataclass(slots=True)
class RecordingProcessor:
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        skip_reason = operation.skip_reason
        if skip_reason is None:
            raise AssertionError("skip operation missing reason")
        self.calls.append(("skip", skip_reason.value))

    async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
        self.calls.append(("index", operation.require_relative_path()))

    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        self.calls.append(("delete", operation.require_relative_path()))

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        self.calls.append(("failed", str(exc)))


@dataclass(slots=True)
class RecordingProcessorFactory(StorageEventOperationProcessorFactory):
    processor: RecordingProcessor

    def processor_for_project(
        self,
        project: ProjectRuntimeReference,
    ) -> RecordingProcessor:
        return self.processor


async def test_run_local_watch_event_indexing_normalizes_changes_and_dispatches(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "local-project"
    project_root.mkdir()
    note_path = project_root / "notes" / "a.md"
    note_path.parent.mkdir()
    note_path.write_text("# A\n", encoding="utf-8")

    resolver = RecordingProjectResolver(project_path="local-project")
    processor = RecordingProcessor()

    result = await run_local_watch_event_indexing(
        LocalWatchEventIndexRequest(
            project_root=project_root,
            project_prefix="local-project",
            changes=((Change.added, str(note_path)),),
            event_time="2026-06-20T16:00:00Z",
        ),
        runtime=StorageEventIndexRuntime(
            project_resolver=resolver,
            operation_processor_factory=RecordingProcessorFactory(processor),
        ),
    )

    assert result.as_dict() == {"processed": 1, "failed": 0, "skipped": 0}
    assert resolver.requested_paths == ["local-project"]
    assert processor.calls == [("index", "notes/a.md")]
