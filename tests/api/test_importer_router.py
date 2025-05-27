"""Tests for importer API routes."""

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from basic_memory.schemas.importer import (
    ChatImportResult,
    EntityImportResult,
    ProjectImportResult,
)


@pytest.fixture
def chatgpt_json_content():
    """Sample ChatGPT conversation data for testing."""
    return [
        {
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
                        "content": {
                            "content_type": "text",
                            "parts": ["Hello, this is a test message"],
                        },
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
    ]


@pytest.fixture
def claude_conversations_json_content():
    """Sample Claude conversations data for testing."""
    return [
        {
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
    ]


@pytest.fixture
def claude_projects_json_content():
    """Sample Claude projects data for testing."""
    return [
        {
            "uuid": "test-uuid",
            "name": "Test Project",
            "created_at": "2025-01-05T20:55:32.499880+00:00",
            "updated_at": "2025-01-05T20:56:39.477600+00:00",
            "prompt_template": "# Test Prompt\n\nThis is a test prompt.",
            "docs": [
                {
                    "uuid": "doc-uuid-1",
                    "filename": "Test Document",
                    "content": "# Test Document\n\nThis is test content.",
                    "created_at": "2025-01-05T20:56:39.477600+00:00",
                },
                {
                    "uuid": "doc-uuid-2",
                    "filename": "Another Document",
                    "content": "# Another Document\n\nMore test content.",
                    "created_at": "2025-01-05T20:56:39.477600+00:00",
                },
            ],
        }
    ]


@pytest.fixture
def memory_json_content():
    """Sample memory.json data for testing."""
    return [
        {
            "type": "entity",
            "name": "test_entity",
            "entityType": "test",
            "observations": ["Test observation 1", "Test observation 2"],
        },
        {
            "type": "relation",
            "from": "test_entity",
            "to": "related_entity",
            "relationType": "test_relation",
        },
    ]


async def create_test_upload_file(tmp_path, content):
    """Create a test file for upload."""
    file_path = tmp_path / "test_import.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(content, f)

    return file_path


@pytest.mark.asyncio
async def test_import_chatgpt(
    project_config, client: AsyncClient, tmp_path, chatgpt_json_content, file_service, project_url
):
    """Test importing ChatGPT conversations."""
    # Create a test file
    file_path = await create_test_upload_file(tmp_path, chatgpt_json_content)

    # Create a multipart form with the file
    with open(file_path, "rb") as f:
        files = {"file": ("conversations.json", f, "application/json")}
        data = {"folder": "test_chatgpt"}

        # Send request
        response = await client.post(f"{project_url}/import/chatgpt", files=files, data=data)

    # Check response
    assert response.status_code == 200
    result = ChatImportResult.model_validate(response.json())
    assert result.success is True
    assert result.conversations == 1
    assert result.messages == 2

    # Verify files were created
    conv_path = Path("test_chatgpt") / "20250111-Test_Conversation.md"
    assert await file_service.exists(conv_path)

    content, _ = await file_service.read_file(conv_path)
    assert "# Test Conversation" in content
    assert "Hello, this is a test message" in content
    assert "This is a test response" in content


@pytest.mark.asyncio
async def test_import_chatgpt_invalid_file(client: AsyncClient, tmp_path, project_url):
    """Test importing invalid ChatGPT file."""
    # Create invalid file
    file_path = tmp_path / "invalid.json"
    with open(file_path, "w") as f:
        f.write("This is not JSON")

    # Create multipart form with invalid file
    with open(file_path, "rb") as f:
        files = {"file": ("invalid.json", f, "application/json")}
        data = {"folder": "test_chatgpt"}

        # Send request - this should return an error
        response = await client.post(f"{project_url}/import/chatgpt", files=files, data=data)

    # Check response
    assert response.status_code == 500
    assert "Import failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_import_claude_conversations(
    client: AsyncClient, tmp_path, claude_conversations_json_content, file_service, project_url
):
    """Test importing Claude conversations."""
    # Create a test file
    file_path = await create_test_upload_file(tmp_path, claude_conversations_json_content)

    # Create a multipart form with the file
    with open(file_path, "rb") as f:
        files = {"file": ("conversations.json", f, "application/json")}
        data = {"folder": "test_claude_conversations"}

        # Send request
        response = await client.post(
            f"{project_url}/import/claude/conversations", files=files, data=data
        )

    # Check response
    assert response.status_code == 200
    result = ChatImportResult.model_validate(response.json())
    assert result.success is True
    assert result.conversations == 1
    assert result.messages == 2

    # Verify files were created
    conv_path = Path("test_claude_conversations") / "20250105-Test_Conversation.md"
    assert await file_service.exists(conv_path)

    content, _ = await file_service.read_file(conv_path)
    assert "# Test Conversation" in content
    assert "Hello, this is a test" in content
    assert "Response to test" in content


@pytest.mark.asyncio
async def test_import_claude_conversations_invalid_file(client: AsyncClient, tmp_path, project_url):
    """Test importing invalid Claude conversations file."""
    # Create invalid file
    file_path = tmp_path / "invalid.json"
    with open(file_path, "w") as f:
        f.write("This is not JSON")

    # Create multipart form with invalid file
    with open(file_path, "rb") as f:
        files = {"file": ("invalid.json", f, "application/json")}
        data = {"folder": "test_claude_conversations"}

        # Send request - this should return an error
        response = await client.post(
            f"{project_url}/import/claude/conversations", files=files, data=data
        )

    # Check response
    assert response.status_code == 500
    assert "Import failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_import_claude_projects(
    client: AsyncClient, tmp_path, claude_projects_json_content, file_service, project_url
):
    """Test importing Claude projects."""
    # Create a test file
    file_path = await create_test_upload_file(tmp_path, claude_projects_json_content)

    # Create a multipart form with the file
    with open(file_path, "rb") as f:
        files = {"file": ("projects.json", f, "application/json")}
        data = {"folder": "test_claude_projects"}

        # Send request
        response = await client.post(
            f"{project_url}/import/claude/projects", files=files, data=data
        )

    # Check response
    assert response.status_code == 200
    result = ProjectImportResult.model_validate(response.json())
    assert result.success is True
    assert result.documents == 2
    assert result.prompts == 1

    # Verify files were created
    project_dir = Path("test_claude_projects") / "Test_Project"
    assert await file_service.exists(project_dir / "prompt-template.md")
    assert await file_service.exists(project_dir / "docs" / "Test_Document.md")
    assert await file_service.exists(project_dir / "docs" / "Another_Document.md")

    # Check content
    prompt_content, _ = await file_service.read_file(project_dir / "prompt-template.md")
    assert "# Test Prompt" in prompt_content

    doc_content, _ = await file_service.read_file(project_dir / "docs" / "Test_Document.md")
    assert "# Test Document" in doc_content
    assert "This is test content" in doc_content


@pytest.mark.asyncio
async def test_import_claude_projects_invalid_file(client: AsyncClient, tmp_path, project_url):
    """Test importing invalid Claude projects file."""
    # Create invalid file
    file_path = tmp_path / "invalid.json"
    with open(file_path, "w") as f:
        f.write("This is not JSON")

    # Create multipart form with invalid file
    with open(file_path, "rb") as f:
        files = {"file": ("invalid.json", f, "application/json")}
        data = {"folder": "test_claude_projects"}

        # Send request - this should return an error
        response = await client.post(
            f"{project_url}/import/claude/projects", files=files, data=data
        )

    # Check response
    assert response.status_code == 500
    assert "Import failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_import_memory_json(
    client: AsyncClient, tmp_path, memory_json_content, file_service, project_url
):
    """Test importing memory.json file."""
    # Create a test file
    json_file = tmp_path / "memory.json"
    with open(json_file, "w", encoding="utf-8") as f:
        for entity in memory_json_content:
            f.write(json.dumps(entity) + "\n")

    # Create a multipart form with the file
    with open(json_file, "rb") as f:
        files = {"file": ("memory.json", f, "application/json")}
        data = {"folder": "test_memory_json"}

        # Send request
        response = await client.post(f"{project_url}/import/memory-json", files=files, data=data)

    # Check response
    assert response.status_code == 200
    result = EntityImportResult.model_validate(response.json())
    assert result.success is True
    assert result.entities == 1
    assert result.relations == 1

    # Verify files were created
    entity_path = Path("test_memory_json") / "test" / "test_entity.md"
    assert await file_service.exists(entity_path)

    # Check content
    content, _ = await file_service.read_file(entity_path)
    assert "Test observation 1" in content
    assert "Test observation 2" in content
    assert "test_relation [[related_entity]]" in content


@pytest.mark.asyncio
async def test_import_memory_json_without_folder(
    client: AsyncClient, tmp_path, memory_json_content, file_service, project_url
):
    """Test importing memory.json file without specifying a destination folder."""
    # Create a test file
    json_file = tmp_path / "memory.json"
    with open(json_file, "w", encoding="utf-8") as f:
        for entity in memory_json_content:
            f.write(json.dumps(entity) + "\n")

    # Create a multipart form with the file
    with open(json_file, "rb") as f:
        files = {"file": ("memory.json", f, "application/json")}

        # Send request without destination_folder
        response = await client.post(f"{project_url}/import/memory-json", files=files)

    # Check response
    assert response.status_code == 200
    result = EntityImportResult.model_validate(response.json())
    assert result.success is True
    assert result.entities == 1
    assert result.relations == 1

    # Verify files were created in the root directory
    entity_path = Path("conversations") / "test" / "test_entity.md"
    assert await file_service.exists(entity_path)


@pytest.mark.asyncio
async def test_import_memory_json_invalid_file(client: AsyncClient, tmp_path, project_url):
    """Test importing invalid memory.json file."""
    # Create invalid file
    file_path = tmp_path / "invalid.json"
    with open(file_path, "w") as f:
        f.write("This is not JSON")

    # Create multipart form with invalid file
    with open(file_path, "rb") as f:
        files = {"file": ("invalid.json", f, "application/json")}
        data = {"destination_folder": "test_memory_json"}

        # Send request - this should return an error
        response = await client.post(f"{project_url}/import/memory-json", files=files, data=data)

    # Check response
    assert response.status_code == 500
    assert "Import failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_import_missing_file(client: AsyncClient, tmp_path, project_url):
    """Test importing with missing file."""
    # Send a request without a file
    response = await client.post(f"{project_url}/import/chatgpt", data={"folder": "test_folder"})

    # Check that the request was rejected
    assert response.status_code in [400, 422]  # Either bad request or unprocessable entity


@pytest.mark.asyncio
async def test_import_empty_file(client: AsyncClient, tmp_path, project_url):
    """Test importing an empty file."""
    # Create an empty file
    file_path = tmp_path / "empty.json"
    with open(file_path, "w") as f:
        f.write("")

    # Create multipart form with empty file
    with open(file_path, "rb") as f:
        files = {"file": ("empty.json", f, "application/json")}
        data = {"folder": "test_chatgpt"}

        # Send request
        response = await client.post(f"{project_url}/import/chatgpt", files=files, data=data)

    # Check response
    assert response.status_code == 500
    assert "Import failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_import_malformed_json(client: AsyncClient, tmp_path, project_url):
    """Test importing malformed JSON for all import endpoints."""
    # Create malformed JSON file
    file_path = tmp_path / "malformed.json"
    with open(file_path, "w") as f:
        f.write('{"incomplete": "json"')  # Missing closing brace

    # Test all import endpoints
    endpoints = [
        (f"{project_url}/import/chatgpt", {"folder": "test"}),
        (f"{project_url}/import/claude/conversations", {"folder": "test"}),
        (f"{project_url}/import/claude/projects", {"base_folder": "test"}),
        (f"{project_url}/import/memory-json", {"destination_folder": "test"}),
    ]

    for endpoint, data in endpoints:
        # Create multipart form with malformed JSON
        with open(file_path, "rb") as f:
            files = {"file": ("malformed.json", f, "application/json")}

            # Send request
            response = await client.post(endpoint, files=files, data=data)

        # Check response
        assert response.status_code == 500
        assert "Import failed" in response.json()["detail"]
