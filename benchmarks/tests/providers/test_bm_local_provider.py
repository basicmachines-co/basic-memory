from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from basic_memory_benchmarks.models import RunConfig
from basic_memory_benchmarks.providers.bm_local import BasicMemoryLocalProvider


def test_extract_existing_project_name_from_bm_error() -> None:
    message = (
        "Error adding project: Cannot create project at '/tmp/docs': "
        "path is nested within existing project 'bm-bench-abc123' at '/tmp/docs'."
    )
    assert BasicMemoryLocalProvider._extract_existing_project_name(message) == "bm-bench-abc123"


def test_extract_existing_project_name_none_without_match() -> None:
    message = "Error adding project: unknown failure"
    assert BasicMemoryLocalProvider._extract_existing_project_name(message) is None


def test_extract_existing_project_name_from_wrapped_bm_error() -> None:
    message = (
        "Error adding project: path is nested within existing project \n"
        "'bm-bench-wrap999' at '/tmp/docs'."
    )
    assert BasicMemoryLocalProvider._extract_existing_project_name(message) == "bm-bench-wrap999"


def test_payload_from_call_tool_result_uses_structured_content() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text='{"results":[{"entity_id":"x"}]}')],
        structuredContent={"results": [{"entity_id": "y"}]},
        isError=False,
    )
    payload = BasicMemoryLocalProvider._payload_from_call_tool_result(result)
    assert payload["results"][0]["entity_id"] == "y"


def test_payload_from_call_tool_result_unwraps_structured_result() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text='{"results":[{"entity_id":"x"}]}')],
        structuredContent={"result": {"results": [{"entity_id": "wrapped"}]}},
        isError=False,
    )
    payload = BasicMemoryLocalProvider._payload_from_call_tool_result(result)
    assert payload["results"][0]["entity_id"] == "wrapped"


def test_payload_from_call_tool_result_parses_text_json() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text='{"results":[{"entity_id":"x"}]}')],
        structuredContent=None,
        isError=False,
    )
    payload = BasicMemoryLocalProvider._payload_from_call_tool_result(result)
    assert payload["results"][0]["entity_id"] == "x"


def test_payload_from_call_tool_result_raises_on_error() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text="search failed")],
        structuredContent=None,
        isError=True,
    )
    with pytest.raises(RuntimeError, match="search failed"):
        BasicMemoryLocalProvider._payload_from_call_tool_result(result)


def test_status_json_is_ready_no_changes() -> None:
    payload = {"status": "No changes"}
    assert BasicMemoryLocalProvider._status_json_is_ready(payload)


def test_status_json_is_ready_sync_payload_total_zero() -> None:
    payload = {
        "new": [],
        "modified": [],
        "deleted": [],
        "moves": {},
        "checksums": {},
        "skipped_files": [],
        "total": 0,
    }
    assert BasicMemoryLocalProvider._status_json_is_ready(payload)


def test_status_json_is_not_ready_sync_payload_with_changes() -> None:
    payload = {
        "new": ["a.md"],
        "modified": [],
        "deleted": [],
        "moves": {},
        "checksums": {},
        "skipped_files": [],
        "total": 1,
    }
    assert not BasicMemoryLocalProvider._status_json_is_ready(payload)


def test_status_json_is_not_ready_when_indexing() -> None:
    payload = {"is_indexing": True}
    assert not BasicMemoryLocalProvider._status_json_is_ready(payload)


def test_resolve_bm_command_prefix_default_uses_bm() -> None:
    run_config = RunConfig(
        run_id="r1",
        dataset_id="synthetic",
        dataset_path="dataset.json",
        corpus_dir="docs",
        queries_path="queries.json",
    )
    assert BasicMemoryLocalProvider._resolve_bm_command_prefix(run_config) == ["bm"]


def test_resolve_bm_command_prefix_local_path_uses_uv_project(tmp_path: Path) -> None:
    run_config = RunConfig(
        run_id="r1",
        dataset_id="synthetic",
        dataset_path="dataset.json",
        corpus_dir="docs",
        queries_path="queries.json",
        bm_local_path=str(tmp_path),
    )
    assert BasicMemoryLocalProvider._resolve_bm_command_prefix(run_config) == [
        "uv",
        "run",
        "--project",
        str(tmp_path),
        "basic-memory",
    ]


def test_resolve_bm_command_prefix_local_path_missing_raises() -> None:
    run_config = RunConfig(
        run_id="r1",
        dataset_id="synthetic",
        dataset_path="dataset.json",
        corpus_dir="docs",
        queries_path="queries.json",
        bm_local_path="/path/that/does/not/exist",
    )
    with pytest.raises(ValueError, match="--bm-local-path not found"):
        BasicMemoryLocalProvider._resolve_bm_command_prefix(run_config)


def test_row_to_hit_surfaces_title_for_date_anchoring() -> None:
    """The document title (carrying the session date) reaches hit metadata."""
    from basic_memory_benchmarks.providers.bm_local import BasicMemoryLocalProvider

    row = {
        "title": "locomo-c00-s07 (4:33 pm on 12 July, 2023)",
        "entity_id": 7,
        "file_path": "locomo-c00-s07.md",
        "matched_chunk": "- **Caroline:** I went to an LGBTQ conference two days ago",
        "content": "# Chat session at 4:33 pm on 12 July, 2023\n...",
        "score": 1.13,
        "metadata": {"note_type": "note"},
    }
    hit = BasicMemoryLocalProvider._row_to_hit(row)
    assert hit.metadata["title"] == "locomo-c00-s07 (4:33 pm on 12 July, 2023)"
    assert hit.metadata["note_type"] == "note"  # existing metadata preserved
    assert hit.source_doc_id == "locomo-c00-s07"
    assert "two days ago" in (hit.text or "")
    assert hit.score == 1.13


def test_row_to_hit_without_title_omits_key() -> None:
    from basic_memory_benchmarks.providers.bm_local import BasicMemoryLocalProvider

    hit = BasicMemoryLocalProvider._row_to_hit(
        {"entity_id": 1, "matched_chunk": "text", "file_path": "doc.md"}
    )
    assert "title" not in hit.metadata
