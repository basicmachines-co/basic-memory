from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult
from tau.bridge import Settings
from tau.knowledge import CodingProfile, coding_context
from test_knowledge import git_repo
from test_recovery import boundary


async def test_nested_checkout_does_not_recall_or_capture_parent_memory(tmp_path: Path) -> None:
    git_repo(tmp_path)
    nested = tmp_path / "nested"
    git_repo(nested)
    lifecycle, context, api = boundary()
    context.cwd = nested
    lifecycle.settings = Settings(
        repositories=[CodingProfile(root=tmp_path, repository="org/parent", project="notes")]
    )
    await lifecycle.orient(context)
    api.append_message.assert_not_called()
    await lifecycle.checkpoint(context, "test")
    context.summarize.assert_not_called()
    with pytest.raises(ValueError, match="differs"):
        await lifecycle.persist(
            context,
            kind="transcript",
            capture_id="id",
            source_tip="tip",
            content="private",
            reason="test",
        )
    api.append_entry.assert_not_called()
    call = lifecycle.connection.call
    assert isinstance(call, AsyncMock)
    call.assert_not_called()


async def test_unborn_repository_cannot_claim_a_commit(tmp_path: Path) -> None:
    from tau.knowledge import command

    await command(tmp_path, "git", "init")
    with pytest.raises(ValueError, match="initialized"):
        await coding_context(CodingProfile(root=tmp_path, repository="org/new"), tmp_path)


async def test_coding_profile_transcript_stays_distinct(tmp_path: Path) -> None:
    git_repo(tmp_path)
    lifecycle, context, _api = boundary()
    context.cwd = tmp_path
    lifecycle.settings = Settings(
        repositories=[CodingProfile(root=tmp_path, repository="org/repo", project="notes")]
    )
    lifecycle.connection.call = AsyncMock(
        return_value=CallToolResult(
            content=[], structured_content={"file_path": "transcript.md", "action": "created"}
        )
    )
    await lifecycle.persist(
        context,
        kind="transcript",
        capture_id="id",
        source_tip="tip",
        content="public",
        reason="test",
    )
    assert lifecycle.connection.call.call_args.args[1]["note_type"] == "tau_transcript"
