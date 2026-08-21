"""Tests for import_chatgpt command."""

import json
from datetime import datetime

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app, import_app
from basic_memory.cli.commands import import_chatgpt  # noqa
from basic_memory.config import get_project_config
from basic_memory.importers.chatgpt_importer import UNKNOWN_DATE_SENTINEL

# Set up CLI runner
runner = CliRunner()


@pytest.fixture
def sample_conversation():
    """Sample ChatGPT conversation data for testing."""
    return {
        "title": "Test Conversation",
        "create_time": 1736616594.24054,  # Example timestamp
        "update_time": 1736616603.164995,
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["msg1"]},
            "msg1": {
                "id": "msg1",
                "message": {
                    "id": "msg1",
                    "author": {"role": "user", "name": None, "metadata": {}},
                    "create_time": 1736616594.24054,
                    "content": {"content_type": "text", "parts": ["Hello, this is a test message"]},
                    "status": "finished_successfully",
                    "metadata": {},
                },
                "parent": "root",
                "children": ["msg2"],
            },
            "msg2": {
                "id": "msg2",
                "message": {
                    "id": "msg2",
                    "author": {"role": "assistant", "name": None, "metadata": {}},
                    "create_time": 1736616603.164995,
                    "content": {"content_type": "text", "parts": ["This is a test response"]},
                    "status": "finished_successfully",
                    "metadata": {},
                },
                "parent": "msg1",
                "children": [],
            },
        },
    }


@pytest.fixture
def sample_conversation_with_code():
    """Sample conversation with code block."""
    conversation = {
        "title": "Code Test",
        "create_time": 1736616594.24054,
        "update_time": 1736616603.164995,
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["msg1"]},
            "msg1": {
                "id": "msg1",
                "message": {
                    "id": "msg1",
                    "author": {"role": "assistant", "name": None, "metadata": {}},
                    "create_time": 1736616594.24054,
                    "content": {
                        "content_type": "code",
                        "language": "python",
                        "text": "def hello():\n    print('Hello world!')",
                    },
                    "status": "finished_successfully",
                    "metadata": {},
                },
                "parent": "root",
                "children": [],
            },
            "msg2": {
                "id": "msg2",
                "message": {
                    "id": "msg2",
                    "author": {"role": "assistant", "name": None, "metadata": {}},
                    "create_time": 1736616594.24054,
                    "status": "finished_successfully",
                    "metadata": {},
                },
                "parent": "root",
                "children": [],
            },
        },
    }
    return conversation


@pytest.fixture
def sample_conversation_with_hidden():
    """Sample conversation with hidden messages."""
    conversation = {
        "title": "Hidden Test",
        "create_time": 1736616594.24054,
        "update_time": 1736616603.164995,
        "mapping": {
            "root": {
                "id": "root",
                "message": None,
                "parent": None,
                "children": ["visible", "hidden"],
            },
            "visible": {
                "id": "visible",
                "message": {
                    "id": "visible",
                    "author": {"role": "user", "name": None, "metadata": {}},
                    "create_time": 1736616594.24054,
                    "content": {"content_type": "text", "parts": ["Visible message"]},
                    "status": "finished_successfully",
                    "metadata": {},
                },
                "parent": "root",
                "children": [],
            },
            "hidden": {
                "id": "hidden",
                "message": {
                    "id": "hidden",
                    "author": {"role": "system", "name": None, "metadata": {}},
                    "create_time": 1736616594.24054,
                    "content": {"content_type": "text", "parts": ["Hidden message"]},
                    "status": "finished_successfully",
                    "metadata": {"is_visually_hidden_from_conversation": True},
                },
                "parent": "root",
                "children": [],
            },
        },
    }
    return conversation


@pytest.fixture
def sample_chatgpt_json(tmp_path, sample_conversation):
    """Create a sample ChatGPT JSON file."""
    json_file = tmp_path / "conversations.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump([sample_conversation], f)
    return json_file


def test_import_chatgpt_command_success(tmp_path, sample_chatgpt_json, monkeypatch):
    """Test successful conversation import via command."""
    # Set up test environment
    monkeypatch.setenv("HOME", str(tmp_path))

    # Run import
    result = runner.invoke(import_app, ["chatgpt", str(sample_chatgpt_json)])
    assert result.exit_code == 0
    assert "Import complete" in result.output
    assert "Imported 1 conversations" in result.output
    assert "Containing 2 messages" in result.output


def test_import_chatgpt_command_invalid_json(tmp_path):
    """Test error handling for invalid JSON."""
    # Create invalid JSON file
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("not json")

    result = runner.invoke(import_app, ["chatgpt", str(invalid_file)])
    assert result.exit_code == 1
    assert "Error during import" in result.output


def test_import_chatgpt_with_custom_folder(tmp_path, sample_chatgpt_json, monkeypatch):
    """Test import with custom conversations folder."""
    # Set up test environment

    config = get_project_config()
    config.home = tmp_path
    conversations_folder = "chats"

    # Run import
    result = runner.invoke(
        app,
        [
            "import",
            "chatgpt",
            str(sample_chatgpt_json),
            "--folder",
            conversations_folder,
        ],
    )
    assert result.exit_code == 0

    # Check files in custom folder
    conv_path = tmp_path / conversations_folder / "20250111-Test_Conversation.md"
    assert conv_path.exists()


def test_import_chatgpt_missing_create_time(tmp_path, sample_conversation):
    """Without create_time, the earliest message time wins over update_time (#1276).

    update_time advances whenever a conversation continues, so using it while
    messages carry timestamps would rename the output file on the next export
    and fork a duplicate note. The messages' 2025-01-11 prefix proves the
    immutable rung was chosen over the later update_time day.
    """
    config = get_project_config()
    config.home = tmp_path
    del sample_conversation["create_time"]
    sample_conversation["update_time"] = 1736703003.0  # 2025-01-12, later than messages
    json_file = tmp_path / "conversations.json"
    json_file.write_text(json.dumps([sample_conversation]), encoding="utf-8")

    result = runner.invoke(app, ["import", "chatgpt", str(json_file), "--folder", "chats"])
    assert result.exit_code == 0
    assert "Imported 1 conversations" in result.output

    conv_path = tmp_path / "chats" / "20250111-Test_Conversation.md"
    assert conv_path.exists()


def test_import_chatgpt_update_time_rung_when_no_message_times(tmp_path, sample_conversation):
    """update_time is the fallback only when no message carries a timestamp (#1276)."""
    config = get_project_config()
    config.home = tmp_path
    del sample_conversation["create_time"]
    sample_conversation["update_time"] = 1736703003.0  # 2025-01-12
    for node in sample_conversation["mapping"].values():
        if node.get("message"):
            node["message"]["create_time"] = None
    json_file = tmp_path / "conversations.json"
    json_file.write_text(json.dumps([sample_conversation]), encoding="utf-8")

    result = runner.invoke(app, ["import", "chatgpt", str(json_file), "--folder", "chats"])
    assert result.exit_code == 0
    assert "Imported 1 conversations" in result.output

    conv_path = tmp_path / "chats" / "20250112-Test_Conversation.md"
    assert conv_path.exists()


def test_import_chatgpt_null_create_time(tmp_path, sample_conversation):
    """Null conversation timestamps fall back to the earliest message time (#1276)."""
    config = get_project_config()
    config.home = tmp_path
    sample_conversation["create_time"] = None
    sample_conversation["update_time"] = None
    # msg1 has no usable time either; msg2 keeps its timestamp and becomes the fallback
    sample_conversation["mapping"]["msg1"]["message"]["create_time"] = None
    json_file = tmp_path / "conversations.json"
    json_file.write_text(json.dumps([sample_conversation]), encoding="utf-8")

    result = runner.invoke(app, ["import", "chatgpt", str(json_file), "--folder", "chats"])
    assert result.exit_code == 0
    assert "Imported 1 conversations" in result.output
    assert "Containing 2 messages" in result.output

    conv_path = tmp_path / "chats" / "20250111-Test_Conversation.md"
    assert conv_path.exists()


def test_import_chatgpt_no_timestamps_anywhere_is_deterministic(tmp_path, sample_conversation):
    """With no usable timestamp at all, the epoch sentinel keeps reimports stable (#1276).

    The resolved date names the output file, so an import-time fallback would
    write a duplicate note under a new name on a later reimport.
    """
    config = get_project_config()
    config.home = tmp_path
    sample_conversation["create_time"] = None
    sample_conversation["update_time"] = None
    for node in sample_conversation["mapping"].values():
        if node.get("message"):
            node["message"]["create_time"] = None
    json_file = tmp_path / "conversations.json"
    json_file.write_text(json.dumps([sample_conversation]), encoding="utf-8")

    result = runner.invoke(app, ["import", "chatgpt", str(json_file), "--folder", "chats"])
    assert result.exit_code == 0
    assert "Imported 1 conversations" in result.output

    epoch_prefix = datetime.fromtimestamp(UNKNOWN_DATE_SENTINEL).astimezone().strftime("%Y%m%d")
    conv_path = tmp_path / "chats" / f"{epoch_prefix}-Test_Conversation.md"
    assert conv_path.exists()
