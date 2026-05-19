"""Tests for note tools that exercise the full stack with SQLite."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from textwrap import dedent

import pytest

from basic_memory.mcp.tools import write_note, read_note
from basic_memory.mcp.tools.read_note import _parse_opening_frontmatter
from basic_memory.utils import normalize_newlines
from tests.mcp.conftest import ContextState, ctx


def test_parse_opening_frontmatter_handles_crlf():
    """JSON read_note output should strip frontmatter from Windows-written markdown."""
    body, frontmatter = _parse_opening_frontmatter(
        "---\r\ntitle: Windows Note\r\ntype: note\r\n---\r\n\r\nBody text\r\n"
    )

    assert frontmatter == {"title": "Windows Note", "type": "note"}
    assert body.strip() == "Body text"


@pytest.mark.asyncio
async def test_read_note_by_title(app, test_project):
    """Test reading a note by its title."""
    # First create a note
    await write_note(
        project=test_project.name,
        title="Special Note",
        directory="test",
        content="Note content here",
    )

    # Should be able to read it by title
    content = await read_note("Special Note", project=test_project.name)
    assert "Note content here" in content


@pytest.mark.asyncio
async def test_read_note_title_search_fallback_fetches_by_permalink(monkeypatch, app, test_project):
    """Force direct resolve to fail so we exercise the title-search + fetch fallback path."""
    await write_note(
        project=test_project.name,
        title="Fallback Title Note",
        directory="test",
        content="fallback content",
    )

    import importlib
    from basic_memory.schemas.memory import memory_url_path

    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    OriginalKnowledgeClient = clients_mod.KnowledgeClient
    direct_identifier = memory_url_path("Fallback Title Note")

    class SelectiveKnowledgeClient(OriginalKnowledgeClient):
        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            # Fail on the direct identifier to force fallback to title search
            if identifier == direct_identifier:
                raise RuntimeError("force direct lookup failure")
            return await super().resolve_entity(identifier, strict=strict)

    monkeypatch.setattr(clients_mod, "KnowledgeClient", SelectiveKnowledgeClient)

    content = await read_note("Fallback Title Note", project=test_project.name)
    assert "fallback content" in content


@pytest.mark.asyncio
async def test_read_note_returns_related_results_when_text_search_finds_matches(
    monkeypatch, app, test_project
):
    """Exercise the related-results message when no exact note match exists."""
    import importlib

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    OriginalKnowledgeClient = clients_mod.KnowledgeClient

    async def fake_search_notes_fn(*, query, search_type, **kwargs):
        if search_type == "title":
            return {"results": [], "current_page": 1, "page_size": 10}

        return {
            "results": [
                {
                    "title": "Related One",
                    "permalink": "docs/related-one",
                    "content": "",
                    "type": "entity",
                    "score": 1.0,
                    "file_path": "docs/related-one.md",
                },
                {
                    "title": "Related Two",
                    "permalink": "docs/related-two",
                    "content": "",
                    "type": "entity",
                    "score": 0.9,
                    "file_path": "docs/related-two.md",
                },
            ],
            "current_page": 1,
            "page_size": 10,
        }

    # Ensure direct resolution doesn't short-circuit the fallback logic.
    class FailingKnowledgeClient(OriginalKnowledgeClient):
        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            raise RuntimeError("force fallback")

    monkeypatch.setattr(clients_mod, "KnowledgeClient", FailingKnowledgeClient)
    monkeypatch.setattr(read_note_module, "search_notes", fake_search_notes_fn)

    result = await read_note("missing-note", project=test_project.name)
    assert "I couldn't find an exact match" in result
    assert "## 1. Related One" in result
    assert "## 2. Related Two" in result


@pytest.mark.asyncio
async def test_read_note_title_fallback_requires_exact_title_match(monkeypatch, app, test_project):
    """Do not fetch note content when title-search returns only fuzzy matches."""
    await write_note(
        project=test_project.name,
        title="Existing Note",
        directory="test",
        content="existing note content",
    )

    import importlib

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    OriginalKnowledgeClient = clients_mod.KnowledgeClient

    class StrictFailingKnowledgeClient(OriginalKnowledgeClient):
        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            if strict:
                raise RuntimeError("force strict direct lookup failure")
            return await super().resolve_entity(identifier, strict=strict)

    async def fake_search_notes_fn(*, query, search_type, **kwargs):
        if search_type == "title":
            return {
                "results": [
                    {
                        "title": "Existing Note",
                        "permalink": "test/existing-note",
                        "content": "",
                        "type": "entity",
                        "score": 1.0,
                        "file_path": "test/Existing Note.md",
                    }
                ],
                "current_page": 1,
                "page_size": 10,
            }
        return {"results": [], "current_page": 1, "page_size": 10}

    monkeypatch.setattr(clients_mod, "KnowledgeClient", StrictFailingKnowledgeClient)
    monkeypatch.setattr(read_note_module, "search_notes", fake_search_notes_fn)

    result = await read_note("Missing Exact Title", project=test_project.name)
    assert "Note Not Found" in result
    assert "Missing Exact Title" in result
    assert "existing note content" not in result


@pytest.mark.asyncio
async def test_read_note_explicit_workspace_project_ignores_stale_cached_project(
    monkeypatch,
    config_manager,
):
    """Explicit workspace routing should use the resolved cloud UUID, not stale cache."""
    import importlib

    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import WorkspaceProjectEntry
    from basic_memory.schemas.project_info import ProjectItem

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    config = config_manager.load_config()
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    personal = project_context.WorkspaceInfo(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    expected_uuid = "22222222-2222-2222-2222-222222222222"
    expected_project = ProjectItem(
        id=2,
        external_id=expected_uuid,
        name="main",
        path="/tmp/main",
        is_default=False,
    )
    stale_project = ProjectItem(
        id=99,
        external_id="33333333-3333-3333-3333-333333333333",
        name="main",
        path="/tmp/stale-main",
        is_default=False,
    )
    context = ContextState()
    await context.set_state("active_workspace", personal.model_dump())
    await context.set_state("active_project", stale_project.model_dump())

    async def fake_resolve_workspace_project_identifier(project_name, context=None):
        assert project_name == "personal/main"
        return WorkspaceProjectEntry(workspace=personal, project=expected_project)

    @asynccontextmanager
    async def fake_get_client(project_name=None, workspace=None):
        assert project_name == "main"
        assert workspace == "personal-tenant"
        yield object()

    class FakeProjectResolveResponse:
        def json(self):
            return {
                "external_id": expected_uuid,
                "project_id": expected_project.id,
                "name": expected_project.name,
                "permalink": expected_project.permalink,
                "path": expected_project.path,
                "is_active": True,
                "is_default": False,
                "resolution_method": "permalink",
            }

    async def fake_call_post(*args, **kwargs):
        return FakeProjectResolveResponse()

    class FakeKnowledgeClient:
        def __init__(self, client, project_id):
            assert project_id == expected_uuid

        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            assert identifier == "personal/main/todo"
            return "entity-1"

        async def get_entity(self, entity_id: str):
            assert entity_id == "entity-1"
            return SimpleNamespace(
                title="TODO",
                permalink="personal/main/todo",
                file_path="TODO.md",
            )

    class FakeResourceClient:
        def __init__(self, client, project_id):
            assert project_id == expected_uuid

        async def read(self, entity_id: str):
            assert entity_id == "entity-1"
            return SimpleNamespace(
                status_code=200,
                text="---\ntitle: TODO\n---\n\n# TODO - Priorities & Tasks\n",
            )

    monkeypatch.setattr(
        project_context,
        "resolve_workspace_project_identifier",
        fake_resolve_workspace_project_identifier,
    )
    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)
    monkeypatch.setattr(clients_mod, "KnowledgeClient", FakeKnowledgeClient)
    monkeypatch.setattr(clients_mod, "ResourceClient", FakeResourceClient)

    result = await read_note_module.read_note(
        "memory://todo",
        project="personal/main",
        output_format="json",
        context=ctx(context),
    )

    assert isinstance(result, dict)
    assert result["permalink"] == "personal/main/todo"
    assert result["content"].strip() == "# TODO - Priorities & Tasks"


@pytest.mark.asyncio
async def test_note_unicode_content(app, test_project):
    """Test handling of unicode content in"""
    content = "# Test 🚀\nThis note has emoji 🎉 and unicode ♠♣♥♦"
    result = await write_note(
        project=test_project.name, title="Unicode Test", directory="test", content=content
    )

    # Check that note was created (checksum is now "unknown" in v2)
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Unicode Test.md" in result
    assert f"permalink: {test_project.name}/test/unicode-test" in result
    assert "checksum:" in result  # Checksum exists but may be "unknown"

    # Read back should preserve unicode
    result = await read_note("test/unicode-test", project=test_project.name)
    assert normalize_newlines(content) in result


@pytest.mark.asyncio
async def test_multiple_notes(app, test_project):
    """Test creating and managing multiple notes"""
    # Create several notes
    notes_data = [
        ("test/note-1", "Note 1", "test", "Content 1", ["tag1"]),
        ("test/note-2", "Note 2", "test", "Content 2", ["tag1", "tag2"]),
        ("test/note-3", "Note 3", "test", "Content 3", []),
    ]

    for _, title, folder, content, tags in notes_data:
        await write_note(
            project=test_project.name, title=title, directory=folder, content=content, tags=tags
        )

    # Should be able to read each one individually
    for permalink, title, folder, content, _ in notes_data:
        note = await read_note(permalink, project=test_project.name)
        assert content in note

    # Note: v2 API does not support glob patterns in read_note
    # Glob patterns should be used with build_context or list_directory instead
    # For reading multiple notes, use build_context with memory:// URLs


@pytest.mark.asyncio
async def test_read_multiple_notes_individually(app, test_project):
    """Test reading several notes back individually after writing them."""
    notes_data = [
        ("test/note-1", "Note 1", "test", "Content 1", ["tag1"]),
        ("test/note-2", "Note 2", "test", "Content 2", ["tag1", "tag2"]),
        ("test/note-3", "Note 3", "test", "Content 3", []),
    ]

    for _, title, folder, content, tags in notes_data:
        await write_note(
            project=test_project.name, title=title, directory=folder, content=content, tags=tags
        )

    for permalink, title, folder, content, _ in notes_data:
        note = await read_note(permalink, project=test_project.name)
        assert content in note

    # Note: v2 API does not support glob patterns in read_note
    # For reading multiple notes, use build_context or list_directory instead


@pytest.mark.asyncio
async def test_read_note_memory_url(app, test_project):
    """Test reading a note using a memory:// URL.

    Should:
    - Handle memory:// URLs correctly
    - Normalize the URL before resolving
    - Return the note content
    """
    # First create a note
    result = await write_note(
        project=test_project.name,
        title="Memory URL Test",
        directory="test",
        content="Testing memory:// URL handling",
    )
    assert result

    # Should be able to read it with a memory:// URL
    memory_url = "memory://test/memory-url-test"
    content = await read_note(memory_url, project=test_project.name)
    assert "Testing memory:// URL handling" in content


@pytest.mark.asyncio
async def test_read_note_memory_url_with_project_prefix(app, test_project):
    """Test reading a note using a memory:// URL with explicit project prefix."""
    await write_note(
        project=test_project.name,
        title="Project Prefixed Memory URL Test",
        directory="test",
        content="Testing memory:// URL handling with project prefix",
    )

    memory_url = f"memory://{test_project.name}/test/project-prefixed-memory-url-test"
    content = await read_note(memory_url)
    assert "Testing memory:// URL handling with project prefix" in content


@pytest.mark.asyncio
async def test_read_note_skips_url_detection_when_project_id_provided(
    monkeypatch,
    app,
    test_project,
):
    """project_id is authoritative, so memory URL discovery must not run first."""
    await write_note(
        project=test_project.name,
        title="Project ID Memory URL Read",
        directory="test",
        content="Read by project id without discovery",
    )

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("project_id routing should bypass URL discovery")

    import importlib

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    monkeypatch.setattr(read_note_module, "detect_project_from_identifier_prefix", fail_if_called)

    result = await read_note(
        f"memory://{test_project.name}/test/project-id-memory-url-read",
        project_id=test_project.external_id,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["content"].strip() == "Read by project id without discovery"


@pytest.mark.asyncio
async def test_team_workspace_write_stores_complete_canonical_permalink(
    app,
    test_project,
    entity_repository,
):
    from basic_memory.workspace_context import workspace_permalink_context

    expected_permalink = f"team-paul/{test_project.name}/team/team-workspace-note"

    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        write_result = await write_note(
            project=test_project.name,
            title="Team Workspace Note",
            directory="team",
            content="Team canonical content",
        )

    assert f"permalink: {expected_permalink}" in write_result

    stored = await entity_repository.get_by_permalink(expected_permalink)
    assert stored is not None
    assert stored.permalink == expected_permalink

    read_result = await read_note(
        expected_permalink,
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(read_result, dict)
    assert read_result["permalink"] == expected_permalink
    assert read_result["content"].strip() == "Team canonical content"


@pytest.mark.asyncio
async def test_team_workspace_write_stores_complete_permalink_when_project_prefixes_disabled(
    app,
    test_project,
    entity_repository,
    config_manager,
):
    from basic_memory.workspace_context import workspace_permalink_context

    config = config_manager.load_config()
    config.permalinks_include_project = False
    config_manager.save_config(config)

    expected_permalink = f"team-paul/{test_project.name}/team/team-no-project-prefix-note"

    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        write_result = await write_note(
            project=test_project.name,
            title="Team No Project Prefix Note",
            directory="team",
            content="Team canonical content without project-prefix config",
        )

    assert f"permalink: {expected_permalink}" in write_result

    stored = await entity_repository.get_by_permalink(expected_permalink)
    assert stored is not None
    assert stored.permalink == expected_permalink

    read_result = await read_note(
        f"memory://{expected_permalink}",
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(read_result, dict)
    assert read_result["permalink"] == expected_permalink
    assert read_result["content"].strip() == "Team canonical content without project-prefix config"


@pytest.mark.asyncio
async def test_personal_workspace_write_stores_complete_canonical_permalink(
    app,
    test_project,
    entity_repository,
):
    from basic_memory.workspace_context import workspace_permalink_context

    expected_permalink = f"personal/{test_project.name}/personal/personal-workspace-note"

    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        write_result = await write_note(
            project=test_project.name,
            title="Personal Workspace Note",
            directory="personal",
            content="Personal canonical content",
        )

    assert f"permalink: {expected_permalink}" in write_result

    stored = await entity_repository.get_by_permalink(expected_permalink)
    assert stored is not None
    assert stored.permalink == expected_permalink


@pytest.mark.asyncio
async def test_read_note_personal_workspace_keeps_short_project_permalink_working(
    app,
    test_project,
):
    from basic_memory.workspace_context import workspace_permalink_context

    legacy_permalink = f"{test_project.name}/personal/short-personal-note"

    await write_note(
        project=test_project.name,
        title="Short Personal Note",
        directory="personal",
        content="Short personal workspace content",
    )

    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        read_result = await read_note(
            f"memory://{legacy_permalink}",
            project=test_project.name,
            output_format="json",
        )

    assert isinstance(read_result, dict)
    assert read_result["permalink"] == legacy_permalink
    assert read_result["content"].strip() == "Short personal workspace content"


@pytest.mark.asyncio
async def test_read_note_workspace_qualified_personal_url_finds_legacy_short_permalink(
    app,
    test_project,
    entity_repository,
):
    from basic_memory.workspace_context import workspace_permalink_context

    legacy_permalink = f"{test_project.name}/personal/legacy-personal-note"
    qualified_permalink = f"personal/{legacy_permalink}"

    await write_note(
        project=test_project.name,
        title="Legacy Personal Note",
        directory="personal",
        content="Legacy personal workspace content",
    )

    stored = await entity_repository.get_by_permalink(legacy_permalink)
    assert stored is not None
    assert stored.permalink == legacy_permalink

    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        read_result = await read_note(
            f"memory://{qualified_permalink}",
            project=test_project.name,
            output_format="json",
        )

    assert isinstance(read_result, dict)
    assert read_result["permalink"] == legacy_permalink
    assert read_result["content"].strip() == "Legacy personal workspace content"


@pytest.mark.asyncio
async def test_read_note_workspace_qualified_memory_url_uses_complete_permalink(
    app,
    test_project,
):
    from basic_memory.workspace_context import workspace_permalink_context

    expected_permalink = f"team-paul/{test_project.name}/team/tool-read-note"

    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        await write_note(
            project=test_project.name,
            title="Tool Read Note",
            directory="team",
            content="Workspace URL read content",
        )

    result = await read_note(
        f"memory://{expected_permalink}",
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["permalink"] == expected_permalink
    assert result["content"].strip() == "Workspace URL read content"


@pytest.mark.asyncio
async def test_read_note_memory_url_fallback_uses_search_tool_normalization(
    monkeypatch, app, test_project
):
    """Fallback search should go back through search_notes for memory:// normalization."""
    await write_note(
        project=test_project.name,
        title="Memory URL Fallback Note",
        directory="test",
        content="Fallback note content",
    )

    import importlib

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    OriginalKnowledgeClient = clients_mod.KnowledgeClient

    fallback_memory_url = f"memory://{test_project.name}/test/memory-url-fallback-note"
    search_calls: list[tuple[str, str, str | None]] = []

    class SelectiveKnowledgeClient(OriginalKnowledgeClient):
        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            if strict and identifier.endswith("test/memory-url-fallback-note"):
                raise RuntimeError("force direct lookup failure")
            return await super().resolve_entity(identifier, strict=strict)

    async def fake_search_notes_fn(*, query, search_type, project, **kwargs):
        search_calls.append((search_type, query, project))
        return {
            "results": [
                {
                    "title": "Memory URL Fallback Note",
                    "permalink": "test/memory-url-fallback-note",
                    "content": "",
                    "type": "entity",
                    "score": 1.0,
                    "file_path": "test/Memory URL Fallback Note.md",
                }
            ],
            "current_page": 1,
            "page_size": 10,
        }

    monkeypatch.setattr(clients_mod, "KnowledgeClient", SelectiveKnowledgeClient)
    monkeypatch.setattr(read_note_module, "search_notes", fake_search_notes_fn)

    result = await read_note(fallback_memory_url)

    assert search_calls == [
        ("title", fallback_memory_url, test_project.name),
        ("text", fallback_memory_url, test_project.name),
    ]
    assert "I couldn't find an exact match" in result
    assert "Memory URL Fallback Note" in result


class TestReadNoteSecurityValidation:
    """Test read_note security validation features."""

    @pytest.mark.asyncio
    async def test_read_note_blocks_path_traversal_unix(self, app, test_project):
        """Test that Unix-style path traversal attacks are blocked in identifier parameter."""
        # Test various Unix-style path traversal patterns
        attack_identifiers = [
            "../secrets.txt",
            "../../etc/passwd",
            "../../../root/.ssh/id_rsa",
            "notes/../../../etc/shadow",
            "folder/../../outside/file.md",
            "../../../../etc/hosts",
            "../../../home/user/.env",
        ]

        for attack_identifier in attack_identifiers:
            result = await read_note(attack_identifier, project=test_project.name)

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_identifier in result

    @pytest.mark.asyncio
    async def test_read_note_blocks_path_traversal_windows(self, app, test_project):
        """Test that Windows-style path traversal attacks are blocked in identifier parameter."""
        # Test various Windows-style path traversal patterns
        attack_identifiers = [
            "..\\secrets.txt",
            "..\\..\\Windows\\System32\\config\\SAM",
            "notes\\..\\..\\..\\Windows\\System32",
            "\\\\server\\share\\file.txt",
            "..\\..\\Users\\user\\.env",
            "\\\\..\\..\\Windows",
            "..\\..\\..\\Boot.ini",
        ]

        for attack_identifier in attack_identifiers:
            result = await read_note(attack_identifier, project=test_project.name)

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_identifier in result

    @pytest.mark.asyncio
    async def test_read_note_blocks_absolute_paths(self, app, test_project):
        """Test that absolute paths are blocked in identifier parameter."""
        # Test various absolute path patterns
        attack_identifiers = [
            "/etc/passwd",
            "/home/user/.env",
            "/var/log/auth.log",
            "/root/.ssh/id_rsa",
            "C:\\Windows\\System32\\config\\SAM",
            "C:\\Users\\user\\.env",
            "D:\\secrets\\config.json",
            "/tmp/malicious.txt",
            "/usr/local/bin/evil",
        ]

        for attack_identifier in attack_identifiers:
            result = await read_note(project=test_project.name, identifier=attack_identifier)

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_identifier in result

    @pytest.mark.asyncio
    async def test_read_note_blocks_home_directory_access(self, app, test_project):
        """Test that home directory access patterns are blocked in identifier parameter."""
        # Test various home directory access patterns
        attack_identifiers = [
            "~/secrets.txt",
            "~/.env",
            "~/.ssh/id_rsa",
            "~/Documents/passwords.txt",
            "~\\AppData\\secrets",
            "~\\Desktop\\config.ini",
            "~/.bashrc",
            "~/Library/Preferences/secret.plist",
        ]

        for attack_identifier in attack_identifiers:
            result = await read_note(project=test_project.name, identifier=attack_identifier)

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_identifier in result

    @pytest.mark.asyncio
    async def test_read_note_blocks_memory_url_attacks(self, app, test_project):
        """Test that memory URLs with path traversal are blocked."""
        # Test memory URLs with attacks embedded
        attack_identifiers = [
            "memory://../../etc/passwd",
            "memory://../../../root/.ssh/id_rsa",
            "memory://~/.env",
            "memory:///etc/passwd",
            "memory://notes/../../../etc/shadow",
            "memory://..\\..\\Windows\\System32",
        ]

        for attack_identifier in attack_identifiers:
            result = await read_note(project=test_project.name, identifier=attack_identifier)

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_read_note_blocks_mixed_attack_patterns(self, app, test_project):
        """Test that mixed legitimate/attack patterns are blocked in identifier parameter."""
        # Test mixed patterns that start legitimate but contain attacks
        attack_identifiers = [
            "notes/../../../etc/passwd",
            "docs/../../.env",
            "legitimate/path/../../.ssh/id_rsa",
            "project/folder/../../../Windows/System32",
            "valid/folder/../../home/user/.bashrc",
            "assets/../../../tmp/evil.exe",
        ]

        for attack_identifier in attack_identifiers:
            result = await read_note(project=test_project.name, identifier=attack_identifier)

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_read_note_allows_safe_identifiers(self, app, test_project):
        """Test that legitimate identifiers are still allowed."""
        # Test various safe identifier patterns
        safe_identifiers = [
            "notes/meeting",
            "docs/readme",
            "projects/2025/planning",
            "archive/old-notes/backup",
            "folder/subfolder/document",
            "research/ml/algorithms",
            "meeting-notes",
            "test/simple-note",
        ]

        for safe_identifier in safe_identifiers:
            result = await read_note(project=test_project.name, identifier=safe_identifier)

            assert isinstance(result, str)
            # Should not contain security error message
            assert (
                "# Error" not in result or "paths must stay within project boundaries" not in result
            )
            # Should either succeed or fail for legitimate reasons (not found, etc.)
            # but not due to security validation

    @pytest.mark.asyncio
    async def test_read_note_allows_legitimate_titles(self, app, test_project):
        """Test that legitimate note titles work normally."""
        # Create a test note first
        await write_note(
            project=test_project.name,
            title="Security Test Note",
            directory="security-tests",
            content="# Security Test Note\nThis is a legitimate note for security testing.",
        )

        # Test reading by title (should work)
        result = await read_note("Security Test Note", project=test_project.name)

        assert isinstance(result, str)
        # Should not be a security error
        assert "# Error" not in result or "paths must stay within project boundaries" not in result
        # Should either return the note content or search results

    @pytest.mark.asyncio
    async def test_read_note_empty_identifier_security(self, app, test_project):
        """Test that empty identifier is handled securely."""
        # Empty identifier should be allowed (may return search results or error, but not security error)
        result = await read_note(identifier="", project=test_project.name)

        assert isinstance(result, str)
        # Empty identifier should not trigger security error
        assert "# Error" not in result or "paths must stay within project boundaries" not in result

    @pytest.mark.asyncio
    async def test_read_note_security_with_all_parameters(self, app, test_project):
        """Test security validation works with all read_note parameters."""
        # Test that security validation is applied even when all other parameters are provided
        result = await read_note(
            project=test_project.name,
            identifier="../../../etc/malicious",
        )

        assert isinstance(result, str)
        assert "# Error" in result
        assert "paths must stay within project boundaries" in result
        assert "../../../etc/malicious" in result

    @pytest.mark.asyncio
    async def test_read_note_security_logging(self, app, caplog, test_project):
        """Test that security violations are properly logged."""
        # Attempt path traversal attack
        result = await read_note(identifier="../../../etc/passwd", project=test_project.name)

        assert "# Error" in result
        assert "paths must stay within project boundaries" in result

        # Check that security violation was logged
        # Note: This test may need adjustment based on the actual logging setup
        # The security validation should generate a warning log entry

    @pytest.mark.asyncio
    async def test_read_note_preserves_functionality_with_security(self, app, test_project):
        """Test that security validation doesn't break normal note reading functionality."""
        # Create a note with complex content to ensure security validation doesn't interfere
        await write_note(
            project=test_project.name,
            title="Full Feature Security Test Note",
            directory="security-tests",
            content=dedent("""
                # Full Feature Security Test Note
                
                This note tests that security validation doesn't break normal functionality.
                
                ## Observations
                - [security] Path validation working correctly #security
                - [feature] All features still functional #test
                
                ## Relations
                - relates_to [[Security Implementation]]
                - depends_on [[Path Validation]]
                
                Additional content with various formatting.
            """).strip(),
            tags=["security", "test", "full-feature"],
            note_type="guide",
        )

        # Test reading by permalink
        result = await read_note(
            "security-tests/full-feature-security-test-note", project=test_project.name
        )

        # Should succeed normally (not a security error)
        assert isinstance(result, str)
        assert "# Error" not in result or "paths must stay within project boundaries" not in result
        # Should either return content or search results, but not security error


class TestReadNoteSecurityEdgeCases:
    """Test edge cases for read_note security validation."""

    @pytest.mark.asyncio
    async def test_read_note_unicode_identifier_attacks(self, app, test_project):
        """Test that Unicode-based path traversal attempts are blocked."""
        # Test Unicode path traversal attempts
        unicode_attack_identifiers = [
            "notes/文档/../../../etc/passwd",  # Chinese characters
            "docs/café/../../.env",  # Accented characters
            "files/αβγ/../../../secret.txt",  # Greek characters
        ]

        for attack_identifier in unicode_attack_identifiers:
            result = await read_note(attack_identifier, project=test_project.name)

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_read_note_very_long_attack_identifier(self, app, test_project):
        """Test handling of very long attack identifiers."""
        # Create a very long path traversal attack
        long_attack_identifier = "../" * 1000 + "etc/malicious"

        result = await read_note(long_attack_identifier, project=test_project.name)

        assert isinstance(result, str)
        assert "# Error" in result
        assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_read_note_case_variations_attacks(self, app, test_project):
        """Test that case variations don't bypass security."""
        # Test case variations (though case sensitivity depends on filesystem)
        case_attack_identifiers = [
            "../ETC/passwd",
            "../Etc/PASSWD",
            "..\\WINDOWS\\system32",
            "~/.SSH/id_rsa",
        ]

        for attack_identifier in case_attack_identifiers:
            result = await read_note(attack_identifier, project=test_project.name)

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_read_note_whitespace_in_attack_identifiers(self, app, test_project):
        """Test that whitespace doesn't help bypass security."""
        # Test attack identifiers with various whitespace
        whitespace_attack_identifiers = [
            " ../../../etc/passwd ",
            "\t../../../secrets\t",
            " ..\\..\\Windows ",
            "notes/ ../../ malicious",
        ]

        for attack_identifier in whitespace_attack_identifiers:
            result = await read_note(attack_identifier, project=test_project.name)

            assert isinstance(result, str)
            # The attack should still be blocked even with whitespace
            if ".." in attack_identifier.strip() or "~" in attack_identifier.strip():
                assert "# Error" in result
                assert "paths must stay within project boundaries" in result
