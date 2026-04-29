"""Regression tests for the live-MCP-process pre-flight in `bm reset` (#765)."""

from __future__ import annotations

import os

import psutil
import pytest
import typer

from basic_memory.cli.commands import db as db_cmd


class _FakeProc:
    """Minimal stand-in for psutil.Process; only exposes .info."""

    def __init__(self, pid: int, cmdline: list[str] | None):
        self.info = {"pid": pid, "cmdline": cmdline}


def _patch_iter(monkeypatch: pytest.MonkeyPatch, procs) -> None:
    """Replace psutil.process_iter with a fixed iterator.

    Procs is intentionally untyped: tests pass a mix of _FakeProc and
    error-raising stand-ins to exercise the per-process exception path.
    """
    monkeypatch.setattr(
        psutil,
        "process_iter",
        lambda attrs=None: iter(procs),
    )


class TestFindLiveMcpProcesses:
    def test_returns_empty_when_no_mcp_processes(self, monkeypatch):
        _patch_iter(
            monkeypatch,
            [
                _FakeProc(pid=11, cmdline=["python", "-m", "http.server"]),
                _FakeProc(pid=22, cmdline=["bm", "sync"]),
            ],
        )
        assert db_cmd._find_live_mcp_processes() == []

    def test_matches_basic_memory_mcp_invocations(self, monkeypatch):
        _patch_iter(
            monkeypatch,
            [
                # Direct `basic-memory mcp`.
                _FakeProc(pid=101, cmdline=["/usr/bin/python", "basic-memory", "mcp"]),
                # `bm mcp` alias entrypoint — must also match (#765 P1).
                _FakeProc(pid=202, cmdline=["bm", "mcp"]),
                # Python module form, underscore name.
                _FakeProc(
                    pid=303,
                    cmdline=["python", "-m", "basic_memory.cli.main", "mcp"],
                ),
                # Absolute path to the bm script.
                _FakeProc(pid=404, cmdline=["/usr/local/bin/bm", "mcp"]),
                # Windows-style bm.exe.
                _FakeProc(pid=505, cmdline=["C:\\Users\\me\\.venv\\Scripts\\bm.exe", "mcp"]),
                # Should NOT match — `mcp` is a substring of another arg, not a token.
                _FakeProc(pid=606, cmdline=["python", "basic-memory", "mcp-helper"]),
                # Should NOT match — has `mcp` but no basic-memory/bm signature.
                _FakeProc(pid=707, cmdline=["python", "/some/other/server.py", "mcp"]),
            ],
        )
        result = db_cmd._find_live_mcp_processes()
        pids = sorted(pid for pid, _ in result)
        assert pids == [101, 202, 303, 404, 505]

    def test_skips_current_process(self, monkeypatch):
        me = os.getpid()
        _patch_iter(
            monkeypatch,
            [
                _FakeProc(pid=me, cmdline=["python", "basic-memory", "mcp"]),
            ],
        )
        # Self-match is suppressed so the helper can be called from inside
        # `bm reset` without flagging the running process.
        assert db_cmd._find_live_mcp_processes() == []

    def test_skips_processes_with_no_cmdline(self, monkeypatch):
        _patch_iter(
            monkeypatch,
            [
                _FakeProc(pid=1, cmdline=None),  # kernel-style process
                _FakeProc(pid=2, cmdline=[]),
            ],
        )
        assert db_cmd._find_live_mcp_processes() == []

    def test_swallows_per_process_errors(self, monkeypatch):
        """A NoSuchProcess race during iteration must not abort the scan."""

        class _Raising:
            @property
            def info(self):
                raise psutil.NoSuchProcess(pid=999)

        _patch_iter(
            monkeypatch,
            [
                _Raising(),
                _FakeProc(pid=42, cmdline=["python", "basic-memory", "mcp"]),
            ],
        )
        result = db_cmd._find_live_mcp_processes()
        assert [pid for pid, _ in result] == [42]


class TestAbortIfMcpProcessesAlive:
    def test_no_op_when_no_processes(self, monkeypatch):
        monkeypatch.setattr(db_cmd, "_find_live_mcp_processes", lambda: [])
        # Must not raise — destructive work should proceed.
        db_cmd._abort_if_mcp_processes_alive()

    def test_exits_with_pids_when_processes_alive(self, monkeypatch, capsys):
        monkeypatch.setattr(
            db_cmd,
            "_find_live_mcp_processes",
            lambda: [(123, "python basic-memory mcp"), (456, "uv run bm mcp wrapper")],
        )
        with pytest.raises(typer.Exit) as exc_info:
            db_cmd._abort_if_mcp_processes_alive()
        assert exc_info.value.exit_code == 1

        captured = capsys.readouterr()
        # PIDs surface so the user can target the cleanup themselves.
        assert "123" in captured.out
        assert "456" in captured.out
        assert "MCP processes" in captured.out

    def test_prints_platform_specific_cleanup_hint_posix(self, monkeypatch, capsys):
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(
            db_cmd,
            "_find_live_mcp_processes",
            lambda: [(7, "python basic-memory mcp")],
        )
        with pytest.raises(typer.Exit):
            db_cmd._abort_if_mcp_processes_alive()
        out = capsys.readouterr().out
        assert "pgrep -fa 'basic-memory mcp'" in out

    def test_prints_platform_specific_cleanup_hint_windows(self, monkeypatch, capsys):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(
            db_cmd,
            "_find_live_mcp_processes",
            lambda: [(7, "python basic-memory mcp")],
        )
        with pytest.raises(typer.Exit):
            db_cmd._abort_if_mcp_processes_alive()
        out = capsys.readouterr().out
        assert "Get-CimInstance" in out
