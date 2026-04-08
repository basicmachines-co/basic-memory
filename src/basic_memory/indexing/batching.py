"""Deterministic helpers for planning bounded indexing batches."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from basic_memory.indexing.models import IndexBatch, IndexFileMetadata


def build_index_batches(
    paths: Sequence[str],
    metadata_by_path: Mapping[str, IndexFileMetadata],
    *,
    max_files: int,
    max_bytes: int,
) -> list[IndexBatch]:
    """Build deterministic batches bounded by file count and total bytes."""
    if max_files <= 0:
        raise ValueError("max_files must be greater than zero")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")

    ordered_paths = sorted(paths)
    batches: list[IndexBatch] = []
    current_paths: list[str] = []
    current_bytes = 0

    for path in ordered_paths:
        metadata = metadata_by_path.get(path)
        if metadata is None:
            raise KeyError(f"Missing metadata for path: {path}")

        file_bytes = max(metadata.size, 0)

        # Trigger: the next file would overflow the active batch.
        # Why: keep batches memory-bounded and predictable for both local and remote callers.
        # Outcome: flush the current batch before placing the next file.
        if current_paths and (
            len(current_paths) >= max_files or current_bytes + file_bytes > max_bytes
        ):
            batches.append(IndexBatch(paths=current_paths, total_bytes=current_bytes))
            current_paths = []
            current_bytes = 0

        # Trigger: one file is larger than the configured byte budget.
        # Why: we still need to index it, but splitting a single file is out of scope.
        # Outcome: emit a dedicated single-file batch that may exceed max_bytes.
        if file_bytes > max_bytes:
            batches.append(IndexBatch(paths=[path], total_bytes=file_bytes))
            continue

        current_paths.append(path)
        current_bytes += file_bytes

        if len(current_paths) >= max_files or current_bytes == max_bytes:
            batches.append(IndexBatch(paths=current_paths, total_bytes=current_bytes))
            current_paths = []
            current_bytes = 0

    if current_paths:
        batches.append(IndexBatch(paths=current_paths, total_bytes=current_bytes))

    return batches
