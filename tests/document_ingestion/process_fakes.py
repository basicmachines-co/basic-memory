"""Scripted stand-ins for asyncio subprocess handles shared by the extractor adapter tests."""

from __future__ import annotations

import asyncio

import pytest


class FakeStdin:
    """Scripted stdin writer; flags when the adapter starts feeding the child."""

    def __init__(self, *, started: asyncio.Event, broken: bool) -> None:
        self._started = started
        self._broken = broken
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self._started.set()
        self.written += data

    async def drain(self) -> None:
        if self._broken:
            raise BrokenPipeError

    def close(self) -> None:
        self.closed = True


def pipe(data: bytes, *, eof: bool = True) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


class FakeProcess:
    """Stand-in for asyncio's subprocess handle with scripted pipes."""

    def __init__(
        self,
        *,
        returncode: int | None = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        hang: bool = False,
        kill_raises: bool = False,
        stdin_broken: bool = False,
    ) -> None:
        self.returncode = returncode
        self._kill_raises = kill_raises
        self.communicate_started = asyncio.Event()
        self.stdin = FakeStdin(started=self.communicate_started, broken=stdin_broken)
        # A hanging child never closes stdout, so the capped read waits forever.
        self.stdout = pipe(stdout, eof=not hang)
        self.stderr = pipe(stderr)
        self.killed = False
        self.waited = False
        self.argv: tuple[object, ...] = ()

    def kill(self) -> None:
        if self._kill_raises:
            raise ProcessLookupError
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        return self.returncode if self.returncode is not None else -9


def install_fake_process(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> None:
    async def create_subprocess(*args: object, **kwargs: object) -> FakeProcess:
        _ = kwargs
        process.argv = args
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
