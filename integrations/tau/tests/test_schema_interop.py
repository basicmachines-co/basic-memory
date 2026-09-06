"""Real BM schema and cross-checkout recall, using isolated notes and a fake model."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from pydantic import JsonValue, TypeAdapter
from tau.bridge import McpConnection, Settings
from tau.continuity import MemoryLifecycle, Note, records, structured
from tau.knowledge import CodingProfile, command
from tau.results import confirm_write
from tau_coding.extensions import ExtensionAPI, ExtensionContext
from test_continuity import make_session
from test_integration import COMMAND, isolate_basic_memory
from test_knowledge import git_repo


@pytest.mark.skipif(not COMMAND, reason="Set BM_TAU_TEST_COMMAND for real schema interoperability")
async def test_shared_schema_and_cross_checkout_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert COMMAND is not None
    isolate_basic_memory(tmp_path, monkeypatch)
    first = tmp_path / "checkout-a"
    second = tmp_path / "checkout-b"
    unrelated = tmp_path / "other-repository"
    for directory in (first, unrelated):
        git_repo(directory)
    worktree = await command(first, "git", "worktree", "add", "-b", "second-checkout", str(second))
    assert worktree.returncode == 0
    settings = Settings(
        command=COMMAND,
        project="main",
        timeout_seconds=90,
        repositories=[
            CodingProfile(root=first, repository="example/project", project="main"),
            CodingProfile(root=second, repository="example/project", project="main"),
            CodingProfile(root=unrelated, repository="example/other", project="main"),
        ],
    )
    connection = McpConnection(settings)
    await connection.start()
    try:
        for name in ("coding-session", "session", "task", "decision"):
            schema_file = Path(__file__).resolve().parents[1] / "schemas" / f"{name}.md"
            _opening, frontmatter, body = schema_file.read_text().split("---", 2)
            metadata = TypeAdapter(dict[str, JsonValue]).validate_python(
                yaml.safe_load(frontmatter)
            )
            confirm_write(
                await connection.call(
                    "write_note",
                    {
                        "title": metadata["title"],
                        "note_type": "schema",
                        "directory": "schemas",
                        "content": body.strip(),
                        "metadata": metadata,
                        "project": "main",
                        "overwrite": False,
                        "output_format": "json",
                    },
                )
            )
        session = await make_session(first, monkeypatch, **settings.model_dump())
        try:
            async for _event in session.prompt("Keep the astrolabe blue."):
                pass
            confirmed = [
                r
                for r in records(ExtensionContext(session.extension_runtime), "main")
                if r.status == "confirmed"
            ]
            assert len(confirmed) == 1
            path = confirmed[0].file_path
            assert path is not None
            report = structured(
                await connection.call(
                    "schema_validate",
                    {
                        "identifier": path,
                        "project": "main",
                        "output_format": "json",
                    },
                )
            )
            assert isinstance(report, dict)
            assert report["total_notes"] == 1
            assert report["warning_count"] == report["error_count"] == 0
            # This is the hook's repository query, not a Tau-specific receipt lookup.
            result = structured(
                await connection.call(
                    "search_notes",
                    {
                        "note_types": ["coding_session"],
                        "metadata_filters": {"repository": "example/project"},
                        "project": "main",
                        "output_format": "json",
                    },
                )
            )
            assert isinstance(result, dict)
            assert any(hit["file_path"] == path for hit in result["results"])
        finally:
            await session.aclose()

        # Reuse verified Git identity, not Tau's receipt fields, in a foreign-host fixture.
        note = Note.model_validate(
            structured(
                await connection.call(
                    "read_note", {"identifier": path, "project": "main", "output_format": "json"}
                )
            )
        )
        assert note.frontmatter is not None
        foreign_metadata = {
            key: note.frontmatter[key]
            for key in ("project", "started", "repository", "repo_root", "cwd", "branch", "git_sha")
        }
        foreign_metadata["codex_session_id"] = "foreign-session"
        foreign = confirm_write(
            await connection.call(
                "write_note",
                {
                    "title": "Other agent engineering checkpoint",
                    "note_type": "coding_session",
                    "directory": "sessions",
                    "content": "- [next_step] Verify the brass telescope.",
                    "metadata": foreign_metadata,
                    "project": "main",
                    "overwrite": False,
                    "output_format": "json",
                },
            )
        )
        foreign_report = structured(
            await connection.call(
                "schema_validate",
                {"identifier": foreign.file_path, "project": "main", "output_format": "json"},
            )
        )
        assert isinstance(foreign_report, dict)
        assert foreign_report["warning_count"] == foreign_report["error_count"] == 0
        for directory, should_recall in ((second, True), (unrelated, False)):
            context = MagicMock(spec=ExtensionContext)
            context.cwd = directory
            context.branch_entries = []
            api = MagicMock(spec=ExtensionAPI)
            api.append_message = AsyncMock()
            lifecycle = MemoryLifecycle(api, settings, connection)
            await lifecycle.orient(context)
            assert lifecycle.last_error is None
            recalled = api.append_message.call_args.args[0]
            assert ("astrolabe" in recalled) is should_recall
            assert ("brass telescope" in recalled) is should_recall
            assert (foreign.file_path in recalled) is should_recall
    finally:
        await connection.close()
