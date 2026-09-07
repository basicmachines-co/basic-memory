from pathlib import Path
from unittest.mock import AsyncMock

from mcp.types import CallToolResult
from tau.bridge import Settings
from tau.continuity import NAMESPACE, CaptureRecord, Note, digest
from tau.knowledge import CodingProfile
from tau_agent.session import CustomEntry
from test_knowledge import git_repo
from test_recovery import boundary


async def test_foreign_branch_receipt_is_not_recalled_and_topic_obeys_budget(
    tmp_path: Path,
) -> None:
    git_repo(tmp_path)
    lifecycle, context, api = boundary()
    context.cwd = tmp_path
    lifecycle.settings = Settings(
        recall_chars=100,
        repositories=[CodingProfile(root=tmp_path, repository="org/current", project="notes")],
    )
    for kind in ("checkpoint", "transcript"):
        record = CaptureRecord.model_validate(
            {
                "project": "notes",
                "capture_id": kind,
                "kind": kind,
                "status": "confirmed",
                "source_tip": "old",
                "content_digest": digest("original"),
                "file_path": "foreign.md",
            }
        )
        context.branch_entries.append(
            CustomEntry(namespace=NAMESPACE, data=record.model_dump(mode="json"))
        )
    lifecycle.read = AsyncMock(
        side_effect=lambda path: Note(
            file_path=path, content="x" * 200, frontmatter={"repository": "org/foreign"}
        )
    )
    lifecycle.connection.call = AsyncMock(
        return_value=CallToolResult(
            content=[],
            structured_content={
                "results": [{"file_path": "topic.md"}, {"file_path": "never-read.md"}]
            },
        )
    )
    await lifecycle.orient(context, "relevant decision")
    assert lifecycle.last_error is None
    assert lifecycle.last_checkpoint is None
    assert lifecycle.read.await_count == 2
    inserted = api.append_message.call_args.args[0]
    assert "foreign.md" not in inserted
    assert "topic.md" in inserted
    assert "Recall truncated" in inserted
