"""Tests for import_chatgpt command."""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app, import_app
from basic_memory.cli.commands import import_chatgpt  # noqa
from basic_memory.config import get_project_config

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
