"""Tests for typed API clients."""

import pytest
from unittest.mock import MagicMock

from basic_memory.mcp.clients import (
    KnowledgeClient,
    SearchClient,
    MemoryClient,
    DirectoryClient,
    ResourceClient,
    ProjectClient,
)


class TestKnowledgeClient:
    """Tests for KnowledgeClient."""

    def test_init(self):
        """Test client initialization."""
        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "project-123")
        assert client.http_client is mock_http
        assert client.project_id == "project-123"
        assert client._base_path == "/v2/projects/project-123/knowledge"

    @pytest.mark.asyncio
    async def test_create_entity(self, monkeypatch):
        """Test create_entity calls correct endpoint."""
        from basic_memory.mcp.clients import knowledge as knowledge_mod

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "permalink": "test",
            "title": "Test",
            "file_path": "test.md",
            "note_type": "note",
            "content_type": "text/markdown",
            "observations": [],
            "relations": [],
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/knowledge/entities" in url
            return mock_response

        monkeypatch.setattr(knowledge_mod, "call_post", mock_call_post)

        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "proj-123")
        result = await client.create_entity({"title": "Test"})
        assert result.title == "Test"

    @pytest.mark.asyncio
    async def test_resolve_entity(self, monkeypatch):
        """Test resolve_entity returns external_id."""
        from basic_memory.mcp.clients import knowledge as knowledge_mod

        mock_response = MagicMock()
        mock_response.json.return_value = {"external_id": "entity-uuid-123"}

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/knowledge/resolve" in url
            return mock_response

        monkeypatch.setattr(knowledge_mod, "call_post", mock_call_post)

        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "proj-123")
        result = await client.resolve_entity("my-note")
        assert result == "entity-uuid-123"


class TestSearchClient:
    """Tests for SearchClient."""

    def test_init(self):
        """Test client initialization."""
        mock_http = MagicMock()
        client = SearchClient(mock_http, "project-123")
        assert client.http_client is mock_http
        assert client.project_id == "project-123"
        assert client._base_path == "/v2/projects/project-123/search"

    @pytest.mark.asyncio
    async def test_search(self, monkeypatch):
        """Test search calls correct endpoint."""
        from basic_memory.mcp.clients import search as search_mod

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [],
            "current_page": 1,
            "page_size": 10,
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/search/" in url
            assert kwargs.get("params") == {"page": 1, "page_size": 10}
            return mock_response

        monkeypatch.setattr(search_mod, "call_post", mock_call_post)

        mock_http = MagicMock()
        client = SearchClient(mock_http, "proj-123")
        result = await client.search({"text": "query"}, page=1, page_size=10)
        assert result.results == []
        assert result.current_page == 1


class TestMemoryClient:
    """Tests for MemoryClient."""

    def test_init(self):
        """Test client initialization."""
        mock_http = MagicMock()
        client = MemoryClient(mock_http, "project-123")
        assert client.http_client is mock_http
        assert client.project_id == "project-123"
        assert client._base_path == "/v2/projects/project-123/memory"

    @pytest.mark.asyncio
    async def test_build_context(self, monkeypatch):
        """Test build_context calls correct endpoint."""
        from basic_memory.mcp.clients import memory as memory_mod
        from datetime import datetime

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [],
            "metadata": {
                "depth": 1,
                "generated_at": datetime.now().isoformat(),
            },
        }

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/memory/specs/search" in url
            return mock_response

        monkeypatch.setattr(memory_mod, "call_get", mock_call_get)

        mock_http = MagicMock()
        client = MemoryClient(mock_http, "proj-123")
        result = await client.build_context("specs/search")
        assert result.results == []

    @pytest.mark.asyncio
    async def test_recent(self, monkeypatch):
        """Test recent calls correct endpoint."""
        from basic_memory.mcp.clients import memory as memory_mod
        from datetime import datetime

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [],
            "metadata": {
                "depth": 2,
                "generated_at": datetime.now().isoformat(),
            },
        }

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/memory/recent" in url
            params = kwargs.get("params", {})
            assert params.get("timeframe") == "7d"
            assert params.get("depth") == 2
            return mock_response

        monkeypatch.setattr(memory_mod, "call_get", mock_call_get)

        mock_http = MagicMock()
        client = MemoryClient(mock_http, "proj-123")
        result = await client.recent(timeframe="7d", depth=2)
        assert result.results == []
        assert result.metadata.depth == 2

    @pytest.mark.asyncio
    async def test_recent_with_types(self, monkeypatch):
        """Test recent with types filter."""
        from basic_memory.mcp.clients import memory as memory_mod
        from datetime import datetime

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [],
            "metadata": {
                "depth": 1,
                "generated_at": datetime.now().isoformat(),
            },
        }

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/memory/recent" in url
            params = kwargs.get("params", {})
            assert params.get("type") == "note,spec"
            return mock_response

        monkeypatch.setattr(memory_mod, "call_get", mock_call_get)

        mock_http = MagicMock()
        client = MemoryClient(mock_http, "proj-123")
        result = await client.recent(types=["note", "spec"])
        assert result.results == []


class TestDirectoryClient:
    """Tests for DirectoryClient."""

    def test_init(self):
        """Test client initialization."""
        mock_http = MagicMock()
        client = DirectoryClient(mock_http, "project-123")
        assert client.http_client is mock_http
        assert client.project_id == "project-123"
        assert client._base_path == "/v2/projects/project-123/directory"

    @pytest.mark.asyncio
    async def test_list(self, monkeypatch):
        """Test list calls correct endpoint."""
        from basic_memory.mcp.clients import directory as directory_mod

        mock_response = MagicMock()
        mock_response.json.return_value = [{"name": "folder", "type": "directory"}]

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/directory/list" in url
            return mock_response

        monkeypatch.setattr(directory_mod, "call_get", mock_call_get)

        mock_http = MagicMock()
        client = DirectoryClient(mock_http, "proj-123")
        result = await client.list("/")
        assert len(result) == 1
        assert result[0]["name"] == "folder"


class TestResourceClient:
    """Tests for ResourceClient."""

    def test_init(self):
        """Test client initialization."""
        mock_http = MagicMock()
        client = ResourceClient(mock_http, "project-123")
        assert client.http_client is mock_http
        assert client.project_id == "project-123"
        assert client._base_path == "/v2/projects/project-123/resource"

    @pytest.mark.asyncio
    async def test_read(self, monkeypatch):
        """Test read calls correct endpoint."""
        from basic_memory.mcp.clients import resource as resource_mod

        mock_response = MagicMock()
        mock_response.text = "# Note content"

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/resource/entity-123" in url
            return mock_response

        monkeypatch.setattr(resource_mod, "call_get", mock_call_get)

        mock_http = MagicMock()
        client = ResourceClient(mock_http, "proj-123")
        result = await client.read("entity-123")
        assert result.text == "# Note content"


class TestProjectClient:
    """Tests for ProjectClient."""

    def test_init(self):
        """Test client initialization."""
        mock_http = MagicMock()
        client = ProjectClient(mock_http)
        assert client.http_client is mock_http

    @pytest.mark.asyncio
    async def test_list_projects(self, monkeypatch):
        """Test list_projects calls correct endpoint."""
        from basic_memory.mcp.clients import project as project_mod

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "projects": [
                {
                    "id": 1,
                    "external_id": "uuid-123",
                    "name": "test-project",
                    "path": "/path/to/project",
                    "is_default": True,
                }
            ],
            "default_project": "test-project",
        }

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects" in url
            return mock_response

        monkeypatch.setattr(project_mod, "call_get", mock_call_get)

        mock_http = MagicMock()
        client = ProjectClient(mock_http)
        result = await client.list_projects()
        assert len(result.projects) == 1
        assert result.projects[0].name == "test-project"
        assert result.default_project == "test-project"
