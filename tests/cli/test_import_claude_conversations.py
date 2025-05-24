"""Tests for import_claude command (chat conversations)."""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands import import_claude_conversations  # noqa
from basic_memory.config import config

# Set up CLI runner
runner = CliRunner()


@pytest.fixture
def sample_conversation():
    """Sample conversation data for testing."""
    return {
        "uuid": "test-uuid",
        "name": "Test Conversation",
        "created_at": "2025-01-05T20:55:32.499880+00:00",
        "updated_at": "2025-01-05T20:56:39.477600+00:00",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "text": "Hello, this is a test",
                "sender": "human",
                "created_at": "2025-01-05T20:55:32.499880+00:00",
                "content": [{"type": "text", "text": "Hello, this is a test"}],
            },
            {
                "uuid": "msg-2",
                "text": "Response to test",
                "sender": "assistant",
                "created_at": "2025-01-05T20:55:40.123456+00:00",
                "content": [{"type": "text", "text": "Response to test"}],
            },
        ],
    }


@pytest.fixture
def sample_conversations_json(tmp_path, sample_conversation):
    """Create a sample conversations.json file."""
    json_file = tmp_path / "conversations.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump([sample_conversation], f)
    return json_file


def test_import_conversations_command_file_not_found(tmp_path):
    """Test error handling for nonexistent file."""
    nonexistent = tmp_path / "nonexistent.json"
    result = runner.invoke(app, ["import", "claude", "conversations", str(nonexistent)])
    assert result.exit_code == 1
    assert "File not found" in result.output


def test_import_conversations_command_success(tmp_path, sample_conversations_json, monkeypatch):
    """Test successful conversation import via command."""
    # Set up test environment
    monkeypatch.setenv("HOME", str(tmp_path))

    # Run import
    result = runner.invoke(
        app, ["import", "claude", "conversations", str(sample_conversations_json)]
    )
    assert result.exit_code == 0
    assert "Import complete" in result.output
    assert "Imported 1 conversations" in result.output
    assert "Containing 2 messages" in result.output


def test_import_conversations_command_invalid_json(tmp_path):
    """Test error handling for invalid JSON."""
    # Create invalid JSON file
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("not json")

    result = runner.invoke(app, ["import", "claude", "conversations", str(invalid_file)])
    assert result.exit_code == 1
    assert "Error during import" in result.output


def test_import_conversations_with_custom_folder(tmp_path, sample_conversations_json, monkeypatch):
    """Test import with custom conversations folder."""
    # Set up test environment
    config.home = tmp_path
    conversations_folder = "chats"

    # Run import
    result = runner.invoke(
        app,
        [
            "import",
            "claude",
            "conversations",
            str(sample_conversations_json),
            "--folder",
            conversations_folder,
        ],
    )
    assert result.exit_code == 0

    # Check files in custom folder
    conv_path = tmp_path / conversations_folder / "20250105-Test_Conversation.md"
    assert conv_path.exists()


def test_import_conversation_with_attachments(tmp_path):
    """Test importing conversation with attachments."""
    # Create conversation with attachments
    conversation = {
        "uuid": "test-uuid",
        "name": "Test With Attachments",
        "created_at": "2025-01-05T20:55:32.499880+00:00",
        "updated_at": "2025-01-05T20:56:39.477600+00:00",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "text": "Here's a file",
                "sender": "human",
                "created_at": "2025-01-05T20:55:32.499880+00:00",
                "content": [{"type": "text", "text": "Here's a file"}],
                "attachments": [
                    {"file_name": "test.txt", "extracted_content": "Test file content"}
                ],
            }
        ],
    }

    json_file = tmp_path / "with_attachments.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump([conversation], f)

    # Set up environment
    config.home = tmp_path

    # Run import
    result = runner.invoke(app, ["import", "claude", "conversations", str(json_file)])
    assert result.exit_code == 0

    # Check attachment formatting
    conv_path = tmp_path / "conversations/20250105-Test_With_Attachments.md"
    content = conv_path.read_text(encoding="utf-8")
    assert "**Attachment: test.txt**" in content
    assert "```" in content
    assert "Test file content" in content
