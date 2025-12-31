"""Tests for the read_content MCP tool security validation.

We keep these tests focused on path boundary/security checks, and rely on
`tests/mcp/test_tool_resource.py` for full-stack content-type behavior.
"""

from __future__ import annotations

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
        result = await read_content.fn(project=test_project.name, path=attack_path)
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
        result = await read_content.fn(project=test_project.name, path=attack_path)
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
        result = await read_content.fn(project=test_project.name, path=attack_path)
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
        result = await read_content.fn(project=test_project.name, path=attack_path)
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
        result = await read_content.fn(project=test_project.name, path=attack_path)
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
        result = await read_content.fn(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]


@pytest.mark.asyncio
async def test_read_content_very_long_attack_path(client, test_project):
    long_attack = "../" * 1000 + "etc/passwd"
    result = await read_content.fn(project=test_project.name, path=long_attack)
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
        result = await read_content.fn(project=test_project.name, path=attack_path)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]


@pytest.mark.asyncio
async def test_read_content_allows_safe_path_integration(client, test_project):
    await write_note.fn(
        project=test_project.name,
        title="Meeting",
        folder="notes",
        content="This is a safe note for read_content()",
    )

    result = await read_content.fn(project=test_project.name, path="notes/meeting")
    assert result["type"] == "text"
    assert "safe note" in result["text"]


@pytest.mark.asyncio
async def test_read_content_empty_path_does_not_trigger_security_error(client, test_project):
    try:
        result = await read_content.fn(project=test_project.name, path="")
        if isinstance(result, dict) and result.get("type") == "error":
            assert "paths must stay within project boundaries" not in result.get("error", "")
    except ToolError:
        # Acceptable: resource resolution may treat empty path as not-found.
        pass


