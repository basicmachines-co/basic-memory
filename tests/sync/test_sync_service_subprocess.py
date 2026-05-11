"""Tests for safe subprocess usage in sync scan optimizations."""

from datetime import datetime
import sys

import pytest

import basic_memory.sync.sync_service as sync_service_module


class _FakeProcess:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, b""


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Windows path uses Python scan fallback")
async def test_quick_count_files_uses_exec_without_shell(monkeypatch, sync_service, tmp_path):
    """Directory names with shell metacharacters must be passed as exec args."""
    directory = tmp_path / 'project "quoted"; echo unsafe'
    captured_args: list[tuple[str, ...]] = []

    async def fail_if_shell_called(*args, **kwargs):
        raise AssertionError("create_subprocess_shell must not be used")

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args.append(args)
        return _FakeProcess(stdout=b"/project/a.md\n/project/b.md\n")

    monkeypatch.setattr(sync_service_module.asyncio, "create_subprocess_shell", fail_if_shell_called)
    monkeypatch.setattr(
        sync_service_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    count = await sync_service._quick_count_files(directory)

    assert count == 2
    assert captured_args == [("find", str(directory), "-type", "f")]


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Windows path uses Python scan fallback")
async def test_scan_directory_modified_since_uses_exec_without_shell(
    monkeypatch,
    sync_service,
    tmp_path,
):
    """Incremental scan should pass the timestamp and directory as exec args."""
    directory = tmp_path / 'project $(echo unsafe)'
    since_timestamp = 1_700_000_000.0
    since_date = datetime.fromtimestamp(since_timestamp).strftime("%Y-%m-%d %H:%M:%S")
    captured_args: list[tuple[str, ...]] = []

    async def fail_if_shell_called(*args, **kwargs):
        raise AssertionError("create_subprocess_shell must not be used")

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args.append(args)
        return _FakeProcess(
            stdout=(
                f"{directory / 'notes' / 'a.md'}\n"
                f"{directory / 'notes' / 'b.md'}\n"
            ).encode()
        )

    monkeypatch.setattr(sync_service_module.asyncio, "create_subprocess_shell", fail_if_shell_called)
    monkeypatch.setattr(
        sync_service_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    paths = await sync_service._scan_directory_modified_since(directory, since_timestamp)

    assert paths == ["notes/a.md", "notes/b.md"]
    assert captured_args == [
        ("find", str(directory), "-type", "f", "-newermt", since_date),
    ]
