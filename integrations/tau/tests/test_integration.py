"""Real BM round trip; opt in with BM_TAU_TEST_COMMAND=/absolute/path/to/bm."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tau.bridge import McpConnection, Settings, discover_sync
from tau.extension import confirm_write, make_tool, tool_result
from tau_coding.extensions import ExtensionRuntime
from tau_coding.extensions.runtime import BoundSession
from tau_coding.resources import TauResourcePaths

COMMAND = os.environ.get("BM_TAU_TEST_COMMAND")


@pytest.mark.skipif(
    not COMMAND, reason="Set BM_TAU_TEST_COMMAND for the isolated real BM round trip"
)
async def test_real_basic_memory_roundtrip_and_fresh_recall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert COMMAND is not None
    isolate_basic_memory(tmp_path, monkeypatch)
    cfg = Settings(command=COMMAND, project="main", timeout_seconds=90)
    tools = discover_sync(cfg)
    by_name = {tool.name: tool for tool in tools}
    assert {"write_note", "read_note", "search_notes", "recent_activity"} <= by_name.keys()
    connection = McpConnection(cfg)
    await connection.start()
    try:
        result = await connection.call(
            "write_note",
            {
                "title": "Tau continuity checkpoint",
                "content": "- [decision] Keep the astrolabe blue.",
                "directory": "checkpoints",
                "project": "main",
                "output_format": "json",
                "overwrite": False,
            },
        )
        receipt = confirm_write(result)
        note = await make_tool(connection, by_name["read_note"]).execute(
            "read", {"identifier": receipt.file_path, "project": "main"}
        )
        assert "astrolabe blue" in note.text
        search = tool_result(
            await connection.call("search_notes", {"query": "astrolabe", "project": "main"})
        )
        assert "Tau continuity checkpoint" in search.text
    finally:
        await connection.close()
    config = tmp_path / "tau-config.json"
    config.write_text(cfg.model_dump_json())
    monkeypatch.setenv("TAU_BASIC_MEMORY_CONFIG", str(config))
    runtime = ExtensionRuntime(built_in_extensions=())
    runtime.load(
        TauResourcePaths(root=tmp_path / ".tau"),
        extra_paths=[Path(__file__).resolve().parents[1]],
        include_resource_dirs=False,
    )
    assert not runtime.diagnostics
    assert len(runtime.compose_tools([])) == len(tools)
    session = MagicMock(spec=BoundSession)
    session.is_running = False
    session.active_branch_entries = ()
    session.cwd = tmp_path
    runtime.bind(session)
    try:
        await runtime.emit_session_start("startup")
        assert not runtime.diagnostics
        assert "Tau continuity checkpoint" in session.append_context_message.call_args.args[0]
    finally:
        await runtime.emit_session_shutdown("quit")
    assert not runtime.diagnostics
    assert (tmp_path / "notes" / receipt.file_path).exists()


def isolate_basic_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith(("BASIC_MEMORY_", "LOGFIRE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "notes"))
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("BASIC_MEMORY_NO_PROMOS", "1")
    monkeypatch.setenv("BASIC_MEMORY_AUTO_UPDATE", "false")
    monkeypatch.setenv("BASIC_MEMORY_LOGFIRE_ENABLED", "false")
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")
    monkeypatch.setenv("BASIC_MEMORY_FORCE_LOCAL", "true")


@pytest.mark.skipif(not COMMAND, reason="Set BM_TAU_TEST_COMMAND for real continuity")
async def test_real_bm_checkpoint_transcript_reload_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tau.continuity import records
    from tau_coding.extensions import ExtensionContext
    from test_continuity import make_session

    assert COMMAND is not None
    isolate_basic_memory(tmp_path, monkeypatch)
    overrides = {
        "command": COMMAND,
        "args": ["mcp", "--transport", "stdio"],
        "project": "main",
        "timeout_seconds": 90,
        "capture_transcript": True,
    }
    session = await make_session(tmp_path, monkeypatch, **overrides)
    async for _ in session.prompt("Keep the astrolabe blue."):
        pass
    confirmed = [
        r
        for r in records(ExtensionContext(session.extension_runtime), "main")
        if r.status == "confirmed"
    ]
    assert len(confirmed) == 3
    for receipt in confirmed:
        assert receipt.file_path is not None
        assert (tmp_path / "notes" / receipt.file_path).exists()
    await session.compact()
    assert any("checkpoint confirmed before compaction" in str(m) for m in session.messages)
    await session.reload()
    assert any("Active branch checkpoint" in str(m) for m in session.messages)
    assert not session.resource_diagnostics
    await session.aclose()
    resumed = await make_session(tmp_path, monkeypatch, **overrides)
    assert any("astrolabe" in str(m) for m in resumed.messages)
    assert not resumed.resource_diagnostics
    await resumed.aclose()
    assert len(list((tmp_path / "notes" / "tau").rglob("*.md"))) == 3
