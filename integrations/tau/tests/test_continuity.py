"""Exercise actual Tau sessions, storage, reload, and MCP subprocesses."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from tau.bridge import McpConnection, Settings
from tau.continuity import NAMESPACE, MemoryLifecycle, records
from tau_agent.messages import AssistantMessage, TextContent, ThinkingContent
from tau_agent.provider_events import AssistantDoneEvent
from tau_agent.session import CustomEntry, JsonlSessionStorage
from tau_agent.types import JSONValue as JsonValue
from tau_ai import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig, TauResourcePaths
from tau_coding.extensions import ExtensionAPI, ExtensionContext

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = """## Objective
Keep the astrolabe blue.
## Decisions
- [decision] Blue is required.
## Verified work
No tests claimed.
## Unfinished work
- [task] Paint the astrolabe.
## Next action
Paint it blue.
## Observations
- [finding] The color is blue.
## Relations
"""


async def make_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: object
) -> CodingSession:
    cfg = Settings.model_validate(
        {
            "command": sys.executable,
            "args": [str(ROOT / "tests/fake_server.py"), str(tmp_path / "notes.json")],
            "project": "notes",
            **overrides,
        }
    )
    config = tmp_path / "bm.json"
    config.write_text(cfg.model_dump_json())
    monkeypatch.setenv("TAU_BASIC_MEMORY_CONFIG", str(config))
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[
                            ThinkingContent(thinking="PRIVATE-REASONING"),
                            TextContent(text=SUMMARY),
                        ],
                        stop_reason="stop",
                    ),
                )
            ]
            for _ in range(100)
        ]
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake",
            system="test",
            cwd=tmp_path,
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            session_id="session-one",
            resource_paths=TauResourcePaths(root=tmp_path / "tau", agents_root=tmp_path / "agents"),
            extensions_enabled=False,
            extension_paths=(ROOT,),
            auto_compact_enabled=False,
        )
    )
    await session.emit_pending_session_start()
    assert not session.resource_diagnostics
    return session


async def test_real_reload_receipts_and_no_duplicate_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await make_session(tmp_path, monkeypatch, capture_transcript=True)
    async for _ in session.prompt("Keep the astrolabe blue."):
        pass
    notes = json.loads((tmp_path / "notes.json").read_text())
    assert len(notes) == 3  # two public messages and one synthesized checkpoint
    assert "PRIVATE-REASONING" not in json.dumps(notes)
    assert not session.resource_diagnostics
    receipts = records(ExtensionContext(session.extension_runtime), "notes")
    assert len([r for r in receipts if r.status == "confirmed"]) == 3
    await session.reload()
    assert len(json.loads((tmp_path / "notes.json").read_text())) == 3
    assert any("Active branch checkpoint" in str(m) for m in session.messages)
    assert not session.queued_messages.follow_up
    assert not session.resource_diagnostics
    await session.aclose()
    assert len(json.loads((tmp_path / "notes.json").read_text())) == 3


@pytest.mark.parametrize("kind", ["manual", "detailed", "threshold", "overflow"])
async def test_actual_pre_compaction_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    session = await make_session(tmp_path, monkeypatch, capture_knowledge=False)
    async for _ in session.prompt("Old decision: blue. " * 15000):
        pass
    async for _ in session.prompt("Recent context. " * 15000):
        pass
    assert not (tmp_path / "notes.json").exists()
    if kind == "manual":
        await session.compact()
    elif kind == "detailed":
        await session.compact_detailed()
    elif kind == "threshold":
        session.set_auto_compaction_enabled(True)
        session._auto_compact_token_threshold = 1
        assert await session._maybe_auto_compact()
    else:
        assert await session._try_overflow_compact(context=session._diagnostic_context())
    notes = json.loads((tmp_path / "notes.json").read_text())
    assert len(notes) == 1
    assert next(iter(notes.values()))["frontmatter"]["reason"] == "compaction:" + (
        "manual" if kind == "detailed" else kind
    )
    entries = await session.session_entries()
    receipt_index = next(
        i
        for i, e in enumerate(entries)
        if isinstance(e, CustomEntry)
        and e.namespace == NAMESPACE
        and e.data["status"] == "confirmed"
    )
    compaction_index = next(i for i, e in enumerate(entries) if e.type == "compaction")
    assert receipt_index < compaction_index
    assert not session.resource_diagnostics
    await session.aclose()


async def test_shutdown_summarizes_without_another_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await make_session(tmp_path, monkeypatch, capture_knowledge=False)
    async for _ in session.prompt("Keep the astrolabe blue."):
        pass
    assert not (tmp_path / "notes.json").exists()
    await session.aclose()
    notes = json.loads((tmp_path / "notes.json").read_text())
    assert len(notes) == 1
    assert next(iter(notes.values()))["frontmatter"]["reason"] == "shutdown"


async def test_pending_receipt_reconciles_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await make_session(
        tmp_path, monkeypatch, capture_knowledge=False, summarize_on_shutdown=False
    )
    context = ExtensionContext(session.extension_runtime)
    cfg = Settings(
        command=sys.executable,
        args=[str(ROOT / "tests/fake_server.py"), str(tmp_path / "reconcile.json")],
        project="notes",
    )
    connection = McpConnection(cfg)
    await connection.start()
    api = ExtensionAPI(session.extension_runtime, "test")
    lifecycle = MemoryLifecycle(api, cfg, connection)
    real_append = api.append_entry

    async def fail_confirmation(namespace: str, data: dict[str, JsonValue]) -> None:
        if data["status"] == "confirmed":
            raise OSError("receipt disk failure")
        await real_append(namespace, data)

    api.append_entry = AsyncMock(side_effect=fail_confirmation)
    try:
        with pytest.raises(OSError):
            await lifecycle.persist(
                context,
                kind="transcript",
                capture_id="capture",
                source_tip="tip",
                content="public",
                reason="test",
            )
        assert records(context, "notes")[-1].status == "pending"
        api.append_entry = AsyncMock(side_effect=real_append)
        path = await lifecycle.persist(
            context,
            kind="transcript",
            capture_id="capture",
            source_tip="tip",
            content="ignored",
            reason="replay",
        )
        assert path.endswith("tau-transcript-capture.md")
        assert len(json.loads((tmp_path / "reconcile.json").read_text())) == 1
        assert records(context, "notes")[-1].status == "confirmed"
    finally:
        await connection.close()
        await session.aclose()


async def test_explicit_checkpoint_input_is_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await make_session(tmp_path, monkeypatch, capture_knowledge=False)
    async for _ in session.prompt("Blue."):
        pass
    events = [
        e
        async for e in session.prompt("/basic-memory-internal-checkpoint color", source="extension")
    ]
    assert events == []  # no agent turn needed to perform the write
    assert (tmp_path / "notes.json").exists()
    await session.aclose()


async def test_connection_restarts_same_instance() -> None:
    connection = McpConnection(
        Settings(command=sys.executable, args=[str(ROOT / "tests/fake_server.py")])
    )
    for _ in range(2):
        await connection.start()
        assert (await connection.call("extra_tool", {})).is_error is False
        await connection.close()
