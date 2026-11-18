"""Source-ID imports preserve separate chats and support subsequent exports."""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app


@pytest.mark.parametrize("source", ["claude", "chatgpt"])
def test_source_ids_preserve_same_title_and_update_after_rename(
    source, tmp_path, monkeypatch, project_config
):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_config.home = tmp_path
    timestamp = "2026-01-02T10:00:00Z"
    records = []
    for number, text in enumerate(["First decision", "Second decision"]):
        source_id = f"session-{number}"
        if source == "claude":
            records.append(
                {
                    "uuid": source_id,
                    "name": "Agent setup",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "chat_messages": [
                        {
                            "uuid": f"message-{number}",
                            "sender": "human",
                            "created_at": timestamp,
                            "text": text,
                        }
                    ],
                }
            )
        else:
            records.append(
                {
                    "conversation_id": source_id,
                    "title": "Agent setup",
                    "create_time": 1767348000,
                    "update_time": 1767348000,
                    "mapping": {
                        "root": {
                            "id": "root",
                            "parent": None,
                            "children": [],
                            "message": {
                                "id": f"message-{number}",
                                "author": {"role": "user"},
                                "create_time": 1767348000,
                                "content": {"content_type": "text", "parts": [text]},
                            },
                        }
                    },
                }
            )
    export = tmp_path / "export.json"
    command = ["import", "claude", "conversations"] if source == "claude" else ["import", "chatgpt"]
    command += [str(export), "--folder", "chats", "--use-source-ids"]
    export.write_text(json.dumps(records), encoding="utf-8")
    first = CliRunner().invoke(app, command)
    assert first.exit_code == 0, first.output
    paths = sorted((tmp_path / "chats").glob("*.md"))
    assert len(paths) == 2
    contents = [path.read_text(encoding="utf-8") for path in paths]
    assert any("First decision" in content for content in contents)
    assert any("Second decision" in content for content in contents)
    assert all("source_conversation_id:" in content for content in contents)

    title_key = "name" if source == "claude" else "title"
    records[0][title_key] = "Renamed conversation"
    records.reverse()
    export.write_text(json.dumps(records), encoding="utf-8")
    second = CliRunner().invoke(app, command)
    assert second.exit_code == 0, second.output
    assert sorted((tmp_path / "chats").glob("*.md")) == paths
    assert any("Renamed conversation" in path.read_text(encoding="utf-8") for path in paths)
