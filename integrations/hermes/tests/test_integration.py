"""
Gated integration tests against a real basic-memory MCP server.

These tests spin up the real `bm mcp` subprocess via the production actor and
exercise every tool through `handle_tool_call`, mirroring the production code
path. They are skipped unless BOTH:

    BM_INTEGRATION=1
    AND `bm` is installed AND `mcp` Python package is importable

A throwaway BM project is created for the test session and removed afterward,
so these tests never touch your real BM projects.

Run them with:

    BM_INTEGRATION=1 uv run --with pytest --with mcp pytest tests/test_integration.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import pytest


# ---- Gating ----

_INTEGRATION_ENABLED = os.environ.get("BM_INTEGRATION") == "1"
_BM_BIN = shutil.which("bm") or (
    os.path.expanduser("~/.local/bin/bm")
    if os.path.isfile(os.path.expanduser("~/.local/bin/bm"))
    else None
)

try:
    import mcp  # noqa: F401

    _MCP_OK = True
except Exception:
    _MCP_OK = False

pytestmark = [
    pytest.mark.skipif(
        not _INTEGRATION_ENABLED,
        reason="set BM_INTEGRATION=1 to run integration tests",
    ),
    pytest.mark.skipif(_BM_BIN is None, reason="bm CLI not on PATH"),
    pytest.mark.skipif(not _MCP_OK, reason="mcp Python package not installed"),
]


# ---- Session-scoped BM project ----


@pytest.fixture(scope="session")
def temp_bm_project():
    """Create a throwaway BM project for the session; remove when done."""
    project_name = f"hermes-bm-test-{uuid.uuid4().hex[:8]}"
    project_dir = tempfile.mkdtemp(prefix=f"{project_name}-")
    # Register
    subprocess.run(
        [_BM_BIN, "project", "add", project_name, project_dir],
        check=False,
        capture_output=True,
        timeout=20,
    )
    yield project_name, project_dir
    # Tear down
    subprocess.run(
        [_BM_BIN, "project", "remove", project_name],
        check=False,
        capture_output=True,
        timeout=20,
    )
    shutil.rmtree(project_dir, ignore_errors=True)


@pytest.fixture
def provider(bm, temp_bm_project, tmp_path):
    """Initialized provider pointing at the temp project."""
    project_name, project_dir = temp_bm_project
    # Pre-write a config file in this test's hermes_home so initialize picks it up
    cfg = {
        "mode": "local",
        "project": project_name,
        "project_path": project_dir,
        "capture_per_turn": True,
        "capture_session_end": True,
        "capture_folder": "test-sessions",
    }
    (tmp_path / "basic-memory.json").write_text(json.dumps(cfg))

    p = bm.BasicMemoryProvider()
    p.initialize(
        session_id=f"integration-{uuid.uuid4().hex[:6]}",
        hermes_home=str(tmp_path),
        platform="cli",
    )
    if not p._initialized:
        pytest.fail("Provider failed to initialize against the real bm MCP server")
    yield p
    p.shutdown()


def _parse_tool_result(raw):
    try:
        d = json.loads(raw)
    except Exception:
        return None
    return d


# ---- Actor smoke ----


def test_actor_starts_and_lists_expected_tools(provider, bm):
    tools = {t["name"] for t in provider._actor.list_tools()}
    expected = set(bm._HERMES_TO_BM.values())
    missing = expected - tools
    assert not missing, f"BM MCP server missing tools we depend on: {missing}"


# ---- Tool surface ----


def test_bm_write_returns_full_permalink(provider, bm):
    title = f"Integration Write Test {uuid.uuid4().hex[:6]}"
    raw = provider.handle_tool_call(
        "bm_write",
        {
            "title": title,
            "content": f"# {title}\n\nbody.\n",
            "folder": "tests",
            "tags": ["integration"],
        },
    )
    permalink = bm._extract_permalink(raw, "")
    assert permalink, f"no permalink extracted from: {raw[:300]}"
    # BM permalinks include the project prefix
    assert permalink.split("/")[0] == provider._project, (
        f"permalink should start with project name: {permalink}"
    )


def test_bm_read_round_trips_a_written_note(provider, bm):
    title = f"Read RT {uuid.uuid4().hex[:6]}"
    body = f"# {title}\n\nMARKER-{uuid.uuid4().hex}\n"
    raw = provider.handle_tool_call(
        "bm_write",
        {
            "title": title,
            "content": body,
            "folder": "tests",
        },
    )
    permalink = bm._extract_permalink(raw, "")
    raw = provider.handle_tool_call("bm_read", {"identifier": permalink})
    d = _parse_tool_result(raw)
    text = (d or {}).get("text") or json.dumps(d or {})
    assert title in text


def test_bm_edit_append_lands_in_note(provider, bm):
    title = f"Append Test {uuid.uuid4().hex[:6]}"
    raw = provider.handle_tool_call(
        "bm_write",
        {
            "title": title,
            "content": f"# {title}\nseed\n",
            "folder": "tests",
        },
    )
    permalink = bm._extract_permalink(raw, "")
    marker = f"APPEND-MARKER-{uuid.uuid4().hex}"
    provider.handle_tool_call(
        "bm_edit",
        {
            "identifier": permalink,
            "operation": "append",
            "content": f"\n{marker}\n",
        },
    )
    raw = provider.handle_tool_call("bm_read", {"identifier": permalink})
    d = _parse_tool_result(raw)
    text = (d or {}).get("text") or json.dumps(d or {})
    assert marker in text


def test_bm_edit_replace_section_swaps_content(provider, bm):
    title = f"ReplaceSection {uuid.uuid4().hex[:6]}"
    body = f"# {title}\n\n## Notes\noriginal-body\n"
    raw = provider.handle_tool_call(
        "bm_write",
        {
            "title": title,
            "content": body,
            "folder": "tests",
        },
    )
    permalink = bm._extract_permalink(raw, "")
    new_marker = f"REPLACED-{uuid.uuid4().hex}"
    provider.handle_tool_call(
        "bm_edit",
        {
            "identifier": permalink,
            "operation": "replace_section",
            "section": "## Notes",
            "content": new_marker,
        },
    )
    raw = provider.handle_tool_call("bm_read", {"identifier": permalink})
    d = _parse_tool_result(raw)
    text = (d or {}).get("text") or json.dumps(d or {})
    assert new_marker in text
    assert "original-body" not in text


def test_bm_search_finds_a_freshly_written_note(provider, bm):
    unique = f"SEARCH-MARKER-{uuid.uuid4().hex}"
    title = f"Search Test {unique}"
    provider.handle_tool_call(
        "bm_write",
        {
            "title": title,
            "content": f"# {title}\nbody.\n",
            "folder": "tests",
        },
    )
    raw = provider.handle_tool_call("bm_search", {"query": unique, "limit": 5})
    d = _parse_tool_result(raw)
    text = (d or {}).get("text") or json.dumps(d or {})
    assert unique in text or title in text


def test_bm_context_returns_results(provider, bm):
    title = f"Context Test {uuid.uuid4().hex[:6]}"
    raw = provider.handle_tool_call(
        "bm_write",
        {
            "title": title,
            "content": f"# {title}\n",
            "folder": "tests",
        },
    )
    permalink = bm._extract_permalink(raw, "")
    raw = provider.handle_tool_call(
        "bm_context",
        {
            "url": f"memory://{permalink}",
            "depth": 1,
        },
    )
    d = _parse_tool_result(raw)
    assert d is not None
    # build_context returns a JSON dict with `results` (and other fields)
    text_blob = json.dumps(d)
    assert "results" in text_blob


def test_bm_move_relocates_note(provider, bm):
    """
    BM permalinks are stable IDs that don't change on move — only the
    file_path moves. So we verify by:
    1. The move response itself reports the new destination
    2. Reading by the original permalink still succeeds (note wasn't lost)
    """
    title = f"Move Test {uuid.uuid4().hex[:6]}"
    raw = provider.handle_tool_call(
        "bm_write",
        {
            "title": title,
            "content": f"# {title}\n",
            "folder": "tests",
        },
    )
    permalink = bm._extract_permalink(raw, "")
    assert permalink, "expected a permalink from bm_write"

    raw = provider.handle_tool_call(
        "bm_move",
        {
            "identifier": permalink,
            "new_folder": "tests/archive",
        },
    )
    d = _parse_tool_result(raw)
    move_text = (d or {}).get("text") or json.dumps(d or {})
    # Move response text reports both the old and new locations
    assert "moved successfully" in move_text.lower() or "moved" in move_text.lower(), (
        f"move response missing success indicator: {move_text[:200]}"
    )
    assert "tests/archive" in move_text, f"move response missing new folder: {move_text[:200]}"

    # Permalink is stable — reading by it should still work
    raw = provider.handle_tool_call("bm_read", {"identifier": permalink})
    d = _parse_tool_result(raw)
    read_text = (d or {}).get("text") or json.dumps(d or {})
    assert title in read_text, "note should still be readable after move"


def test_bm_delete_removes_note(provider, bm):
    title = f"Delete Test {uuid.uuid4().hex[:6]}"
    raw = provider.handle_tool_call(
        "bm_write",
        {
            "title": title,
            "content": f"# {title}\n",
            "folder": "tests",
        },
    )
    permalink = bm._extract_permalink(raw, "")
    provider.handle_tool_call("bm_delete", {"identifier": permalink})
    # Read should now indicate "not found"
    raw = provider.handle_tool_call("bm_read", {"identifier": permalink})
    d = _parse_tool_result(raw)
    text = (d or {}).get("text") or json.dumps(d or {})
    assert "not found" in text.lower() or "no notes found" in text.lower(), (
        f"expected a 'not found' indication, got: {text[:200]}"
    )


# ---- Capture pipeline ----


def test_sync_turn_writes_then_appends_to_same_session_note(provider, bm):
    # First turn — creates the session note
    provider.sync_turn("integration turn-1 user", "integration turn-1 assistant")
    if provider._sync_thread:
        provider._sync_thread.join(timeout=20.0)
    sid_1 = provider._session_note_id
    assert sid_1, "first sync_turn should set _session_note_id"
    assert sid_1.startswith(provider._project + "/"), (
        f"session_note_id should include project prefix, got: {sid_1}"
    )

    # Second turn — should append to the same note
    marker = f"TURN-2-MARKER-{uuid.uuid4().hex}"
    provider.sync_turn("integration turn-2 user", marker)
    if provider._sync_thread:
        provider._sync_thread.join(timeout=20.0)
    sid_2 = provider._session_note_id
    assert sid_2 == sid_1, "session_note_id should NOT change between turns"

    # Verify both turn markers are present in the persisted note
    raw = provider.handle_tool_call("bm_read", {"identifier": sid_1})
    d = _parse_tool_result(raw)
    text = (d or {}).get("text") or json.dumps(d or {})
    assert "integration turn-1 user" in text
    assert marker in text


def test_on_session_end_writes_summary_with_relations(provider, bm):
    # Seed a session note via sync_turn
    provider.sync_turn("first message", "first reply")
    if provider._sync_thread:
        provider._sync_thread.join(timeout=20.0)
    sid = provider._session_note_id
    assert sid

    provider.on_session_end(
        [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first reply"},
        ]
    )

    # Search for the summary
    raw = provider.handle_tool_call(
        "bm_search",
        {
            "query": "Hermes Session Summary",
            "limit": 5,
        },
    )
    d = _parse_tool_result(raw)
    text = (d or {}).get("text") or json.dumps(d or {})
    assert "Hermes Session Summary" in text


def test_prefetch_against_real_bm(provider, bm):
    # Seed a recognizable note
    unique = f"PREFETCH-MARKER-{uuid.uuid4().hex}"
    provider.handle_tool_call(
        "bm_write",
        {
            "title": f"Prefetch Test {unique}",
            "content": f"# Prefetch Test\n{unique}\n",
            "folder": "tests",
        },
    )

    # BM's FTS index is updated synchronously inside the write_note API
    # path (knowledge_router.py:272), so this loop is really only smoothing
    # over the round-trip cost of a few RPCs on a slow runner. prefetch
    # explicitly requests search_type="text" so we don't get pulled onto
    # BM's hybrid path, where vector indexing is async and would race the
    # search.
    budget_secs = 10.0
    deadline = time.monotonic() + budget_secs
    out = ""
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        out = provider.prefetch(unique)
        if out:
            break
        time.sleep(0.25)

    assert out, (
        f"prefetch returned nothing after {attempts} attempt(s) over "
        f"{budget_secs}s; provider._failure_count={provider._failure_count}, "
        f"circuit_open={provider._is_circuit_open()}. "
        f"Either BM didn't index the note in time or prefetch's actor.call "
        f"is timing out internally."
    )
    assert "Basic Memory Recall" in out
    assert unique in out or "Prefetch Test" in out
