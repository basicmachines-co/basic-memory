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
from basic_memory.schemas.search import SearchRetrievalMode


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
            assert kwargs.get("params") is None
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", mock_call_post)

        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "proj-123")
        result = await client.create_entity({"title": "Test"})
        assert result.title == "Test"

    @pytest.mark.asyncio
    async def test_update_entity(self, monkeypatch):
        """Test update_entity calls correct endpoint without fast query params."""

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

        async def mock_call_put(client, url, **kwargs):
            assert "/v2/projects/proj-123/knowledge/entities/entity-123" in url
            assert kwargs.get("params") is None
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_put", mock_call_put)

        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "proj-123")
        result = await client.update_entity("entity-123", {"title": "Test"})
        assert result.title == "Test"

    @pytest.mark.asyncio
    async def test_patch_entity(self, monkeypatch):
        """Test patch_entity calls correct endpoint without fast query params."""

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

        async def mock_call_patch(client, url, **kwargs):
            assert "/v2/projects/proj-123/knowledge/entities/entity-123" in url
            assert kwargs.get("params") is None
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_patch", mock_call_patch)

        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "proj-123")
        result = await client.patch_entity("entity-123", {"operation": "append"})
        assert result.title == "Test"

    @pytest.mark.asyncio
    async def test_resolve_entity(self, monkeypatch):
        """Test resolve_entity returns external_id."""

        mock_response = MagicMock()
        mock_response.json.return_value = {"external_id": "entity-uuid-123"}

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/knowledge/resolve" in url
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", mock_call_post)

        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "proj-123")
        result = await client.resolve_entity("my-note")
        assert result == "entity-uuid-123"

    @pytest.mark.asyncio
    async def test_resolve_entity_response_preserves_project_metadata(self, monkeypatch):
        """Complete resolution responses retain the owning project external ID."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "external_id": "entity-uuid-123",
            "entity_id": 42,
            "project_external_id": "project-uuid-456",
            "permalink": "other-project/notes/my-note",
            "file_path": "notes/My Note.md",
            "title": "My Note",
            "resolution_method": "permalink",
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/knowledge/resolve" in url
            assert kwargs["json"] == {"identifier": "my-note", "strict": True}
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", mock_call_post)

        client = KnowledgeClient(MagicMock(), "proj-123")
        result = await client.resolve_entity_response("my-note", strict=True)

        assert result.external_id == "entity-uuid-123"
        assert result.project_external_id == "project-uuid-456"

    @pytest.mark.asyncio
    async def test_index_file(self, monkeypatch):
        """Test index_file posts the file path to the index-file endpoint."""

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "permalink": "notes/disk-note",
            "title": "Disk Note",
            "file_path": "notes/disk-note.md",
            "note_type": "note",
            "content_type": "text/markdown",
            "observations": [],
            "relations": [],
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/knowledge/index-file" in url
            assert kwargs.get("json") == {"file_path": "notes/disk-note.md"}
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", mock_call_post)

        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "proj-123")
        result = await client.index_file("notes/disk-note.md")
        assert result.file_path == "notes/disk-note.md"

    @pytest.mark.asyncio
    async def test_get_orphans_validates_response(self, monkeypatch):
        """Orphan responses are validated into GraphNode objects."""
        from basic_memory.schemas.v2.graph import GraphNode

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "entities": [
                {
                    "external_id": "entity-uuid-123",
                    "title": "Orphan Note",
                    "file_path": "notes/orphan.md",
                    "note_type": "note",
                }
            ],
            "total": 1,
        }

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/knowledge/orphans" in url
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_get", mock_call_get)

        mock_http = MagicMock()
        client = KnowledgeClient(mock_http, "proj-123")
        result = await client.get_orphans()

        assert len(result) == 1
        assert isinstance(result[0], GraphNode)
        assert result[0].title == "Orphan Note"


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

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", mock_call_post)

        mock_http = MagicMock()
        client = SearchClient(mock_http, "proj-123")
        result = await client.search({"text": "query"}, page=1, page_size=10)
        assert result.results == []
        assert result.current_page == 1
        assert result.total_is_exact is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "retrieval_mode",
        [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID],
    )
    async def test_search_infers_unknown_total_for_legacy_semantic_response(
        self,
        monkeypatch,
        retrieval_mode,
    ):
        """Legacy semantic responses preserve their unknown total semantics."""

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [],
            "current_page": 1,
            "page_size": 10,
            "total": 0,
            "has_more": False,
        }

        async def mock_call_post(client, url, **kwargs):
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", mock_call_post)

        client = SearchClient(MagicMock(), "proj-123")
        result = await client.search({"text": "query", "retrieval_mode": retrieval_mode})

        assert result.total == 0
        assert result.total_is_exact is False

    @pytest.mark.asyncio
    async def test_search_preserves_explicit_total_exactness(self, monkeypatch):
        """Server-provided exactness remains authoritative."""

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [],
            "current_page": 1,
            "page_size": 10,
            "total": 0,
            "total_is_exact": True,
            "has_more": False,
        }

        async def mock_call_post(client, url, **kwargs):
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", mock_call_post)

        client = SearchClient(MagicMock(), "proj-123")
        result = await client.search(
            {"text": "query", "retrieval_mode": SearchRetrievalMode.VECTOR}
        )

        assert result.total_is_exact is True


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

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_get", mock_call_get)

        mock_http = MagicMock()
        client = MemoryClient(mock_http, "proj-123")
        result = await client.build_context("specs/search")
        assert result.results == []

    @pytest.mark.asyncio
    async def test_recent(self, monkeypatch):
        """Test recent calls correct endpoint."""
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

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_get", mock_call_get)

        mock_http = MagicMock()
        client = MemoryClient(mock_http, "proj-123")
        result = await client.recent(timeframe="7d", depth=2)
        assert result.results == []
        assert result.metadata.depth == 2

    @pytest.mark.asyncio
    async def test_recent_with_types(self, monkeypatch):
        """Test recent with types filter."""
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

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_get", mock_call_get)

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

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "nodes": [
                {
                    "name": "folder",
                    "directory_path": "/folder",
                    "type": "directory",
                }
            ],
            "page": 2,
            "page_size": 4,
            "total": 5,
            "has_more": False,
        }

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/directory/list" in url
            assert kwargs["params"] == {
                "dir_name": "/",
                "depth": 2,
                "page": 2,
                "page_size": 4,
                "file_name_glob": "*.md",
            }
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_get", mock_call_get)

        mock_http = MagicMock()
        client = DirectoryClient(mock_http, "proj-123")
        result = await client.list("/", depth=2, file_name_glob="*.md", page=2, page_size=4)
        assert len(result.nodes) == 1
        assert result.nodes[0].name == "folder"
        assert result.page == 2
        assert result.total == 5


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

        mock_response = MagicMock()
        mock_response.text = "# Note content"

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/resource/entity-123" in url
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_get", mock_call_get)

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

        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_get", mock_call_get)

        mock_http = MagicMock()
        client = ProjectClient(mock_http)
        result = await client.list_projects()
        assert len(result.projects) == 1
        assert result.projects[0].name == "test-project"
        assert result.default_project == "test-project"
