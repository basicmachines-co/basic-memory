"""Indexing reports the stored bytes it actually indexed, for work-unit billing.

Cloud bills per-tenant work units from these counts, so a file the checker finds
current must report zero: only content the indexer read and parsed counts.
"""

import pytest

from basic_memory.file_utils import compute_checksum
from basic_memory.index.local_project import LocalProjectIndexRuntimeFactory
from basic_memory.indexing.models import IndexFileJobStatus
from basic_memory.models import Project
from basic_memory.runtime.jobs import RuntimeIndexFileBatchJobRequest, RuntimeObservedIndexFile
from basic_memory.runtime.projects import ProjectRuntimeReference

# Complete frontmatter, so indexing never rewrites the file and the stored bytes
# stay exactly what the test wrote.
NOTE_CONTENT = """---
title: Billing Note
type: note
permalink: notes/billing-note
---

# Billing Note

Unicode counts as bytes, not characters: café, naïve, 漢字.

- [fact] Work units bill on bytes indexed
"""


@pytest.mark.asyncio
async def test_batch_index_reports_bytes_indexed_and_zero_for_current_file(
    test_project: Project,
    project_config,
) -> None:
    path = "notes/billing-note.md"
    (project_config.home / "notes").mkdir(parents=True, exist_ok=True)
    # newline="\n" keeps the file's bytes equal to NOTE_CONTENT on Windows too
    (project_config.home / path).write_text(NOTE_CONTENT, encoding="utf-8", newline="\n")
    expected_bytes = len(NOTE_CONTENT.encode("utf-8"))
    runtime = await LocalProjectIndexRuntimeFactory().runtime_for_project(test_project)
    # The observed checksum lets the checker compare against the index and
    # decide the second pass is current without reading content.
    request = RuntimeIndexFileBatchJobRequest(
        project=ProjectRuntimeReference.from_project(test_project),
        batch_index=0,
        batch_count=1,
        observed_files=(
            RuntimeObservedIndexFile(
                path=path,
                checksum=await compute_checksum(NOTE_CONTENT),
                size=expected_bytes,
            ),
        ),
    )

    first = await runtime.batch_enqueuer.enqueue_index_file_batch(request)
    second = await runtime.batch_enqueuer.enqueue_index_file_batch(request)
    # The local enqueuer runs batches inline, so both calls return results.
    assert first is not None
    assert second is not None

    assert (project_config.home / path).read_text(encoding="utf-8") == NOTE_CONTENT
    assert first.file_results[0].status == IndexFileJobStatus.processed
    assert first.file_results[0].indexed_bytes == expected_bytes
    assert first.indexed_bytes == expected_bytes
    assert second.file_results[0].status == IndexFileJobStatus.current
    assert second.file_results[0].indexed_bytes == 0
    assert second.indexed_bytes == 0


@pytest.mark.asyncio
async def test_single_file_index_reports_bytes_indexed(
    test_project: Project,
    project_config,
) -> None:
    path = "notes/billing-note.md"
    (project_config.home / "notes").mkdir(parents=True, exist_ok=True)
    (project_config.home / path).write_text(NOTE_CONTENT, encoding="utf-8", newline="\n")
    dependencies = await LocalProjectIndexRuntimeFactory().dependencies_for_project(test_project)

    result = await dependencies.file_indexer.index_file(path, source="index")

    assert result.indexed_bytes == len(NOTE_CONTENT.encode("utf-8"))
