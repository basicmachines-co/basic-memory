"""Tests for the read_content MCP tool security validation.

We keep these tests focused on path boundary/security checks, and rely on
`tests/mcp/test_tool_resource.py` for full-stack content-type behavior.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import read_content, write_note


@pytest.mark.asyncio
async def test_read_content_blocks_path_traversal_unix(client, test_project):
    attack_paths = [
        "../secrets.txt",
        "../../etc/passwd",
        "../../../root/.ssh/id_rsa",
        "notes/../../../etc/shadow",
        "folder/../../outside/file.md",
        "../../../../etc/hosts",
        "../../../home/user/.env",
    ]

    for attack_path in attack_paths:
        result = await read_content(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]
        assert attack_path in result["error"]


@pytest.mark.asyncio
async def test_read_content_blocks_path_traversal_windows(client, test_project):
    attack_paths = [
        "..\\secrets.txt",
        "..\\..\\Windows\\System32\\config\\SAM",
        "notes\\..\\..\\..\\Windows\\System32",
        "\\\\server\\share\\file.txt",
        "..\\..\\Users\\user\\.env",
        "\\\\..\\..\\Windows",
        "..\\..\\..\\Boot.ini",
    ]

    for attack_path in attack_paths:
        result = await read_content(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]
        assert attack_path in result["error"]


@pytest.mark.asyncio
async def test_read_content_blocks_absolute_paths(client, test_project):
    attack_paths = [
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

    for attack_path in attack_paths:
        result = await read_content(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]
        assert attack_path in result["error"]


@pytest.mark.asyncio
async def test_read_content_blocks_home_directory_access(client, test_project):
    attack_paths = [
        "~/secrets.txt",
        "~/.env",
        "~/.ssh/id_rsa",
        "~/Documents/passwords.txt",
        "~\\AppData\\secrets",
        "~\\Desktop\\config.ini",
        "~/.bashrc",
        "~/Library/Preferences/secret.plist",
    ]

    for attack_path in attack_paths:
        result = await read_content(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]
        assert attack_path in result["error"]


@pytest.mark.asyncio
async def test_read_content_blocks_memory_url_attacks(client, test_project):
    attack_paths = [
        "memory://../../etc/passwd",
        "memory://../../../root/.ssh/id_rsa",
        "memory://~/.env",
        "memory:///etc/passwd",
    ]

    for attack_path in attack_paths:
        result = await read_content(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]


@pytest.mark.asyncio
async def test_read_content_unicode_path_attacks(client, test_project):
    unicode_attacks = [
        "notes/文档/../../../etc/passwd",
        "docs/café/../../.env",
        "files/αβγ/../../../secret.txt",
    ]

    for attack_path in unicode_attacks:
        result = await read_content(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]


@pytest.mark.asyncio
async def test_read_content_very_long_attack_path(client, test_project):
    long_attack = "../" * 1000 + "etc/passwd"
    result = await read_content(project=test_project.name, path=long_attack)
    assert result["type"] == "error"
    assert "paths must stay within project boundaries" in result["error"]


@pytest.mark.asyncio
async def test_read_content_case_variations_attacks(client, test_project):
    case_attacks = [
        "../ETC/passwd",
        "../Etc/PASSWD",
        "..\\WINDOWS\\system32",
        "~/.SSH/id_rsa",
    ]

    for attack_path in case_attacks:
        result = await read_content(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]


@pytest.mark.asyncio
async def test_read_content_allows_safe_path_integration(client, test_project):
    await write_note(
        project=test_project.name,
        title="Meeting",
        directory="notes",
        content="This is a safe note for read_content()",
    )

    result = await read_content(project=test_project.name, path="notes/meeting")
    assert result["type"] == "text"
    assert "safe note" in result["text"]


@pytest.mark.asyncio
async def test_read_content_workspace_memory_url_routes_with_local_config(
    monkeypatch,
    config_manager,
):
    """Workspace-qualified memory URLs should route even when local projects exist."""
    import importlib

    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import ProjectEntry
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
    )
    from basic_memory.schemas.cloud import WorkspaceInfo
    from basic_memory.schemas.project_info import ProjectItem

    read_content_module = importlib.import_module("basic_memory.mcp.tools.read_content")
    config = config_manager.load_config()
    config.projects["hermes-memory"] = ProjectEntry(
        path=str(config_manager.config_dir.parent / "hermes-memory")
    )
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    personal = WorkspaceInfo(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="main",
        path="/tmp/main",
        is_default=False,
    )
    index = _build_workspace_project_index(
        (personal,),
        (WorkspaceProjectEntry(workspace=personal, project=project),),
    )

    async def fake_index(context=None):
        return index

    @asynccontextmanager
    async def fake_get_project_client(project=None, context=None, project_id=None):
        assert project == "personal/main"
        assert project_id is None
        yield (
            object(),
            SimpleNamespace(
                name="main",
                external_id="11111111-1111-1111-1111-111111111111",
                home=Path("/tmp/main"),
            ),
        )

    async def fake_resolve_project_and_path(client, identifier, project=None, context=None):
        assert identifier == "memory://personal/main/docs/report"
        assert project == "main"
        return None, "personal/main/docs/report", True

    async def fake_resolve_entity_id(client, project_id, url):
        assert project_id == "11111111-1111-1111-1111-111111111111"
        assert url == "personal/main/docs/report"
        return "entity-1"

    class FakeResponse:
        headers = {"content-type": "text/markdown", "content-length": "17"}
        text = "# Routed Content"
        content = b"# Routed Content"

    async def fake_call_get(client, path, **kwargs):
        assert path == "/v2/projects/11111111-1111-1111-1111-111111111111/resource/entity-1"
        return FakeResponse()

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr(read_content_module, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(
        read_content_module,
        "resolve_project_and_path",
        fake_resolve_project_and_path,
    )
    monkeypatch.setattr(read_content_module, "resolve_entity_id", fake_resolve_entity_id)
    monkeypatch.setattr(read_content_module, "call_get", fake_call_get)

    result = await read_content(path="memory://personal/main/docs/report")

    assert result == {
        "type": "text",
        "text": "# Routed Content",
        "content_type": "text/markdown",
        "encoding": "utf-8",
    }


@pytest.mark.asyncio
async def test_read_content_empty_path_does_not_trigger_security_error(client, test_project):
    try:
        result = await read_content(project=test_project.name, path="")
        if isinstance(result, dict) and result.get("type") == "error":
            assert "paths must stay within project boundaries" not in result.get("error", "")
    except ToolError:
        # Acceptable: resource resolution may treat empty path as not-found.
        pass
