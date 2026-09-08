"""Run one extractor over untrusted bytes in a killable, byte-capped child process.

Every native or third-party document parser Basic Memory runs is synchronous and
parses attacker-controlled input. The parent therefore never calls a parser
in-process: it feeds the bytes to ``python -m <worker>`` on stdin, drains both
pipes with a hard ceiling, and kills the child at the deadline. The worker
applies POSIX rlimits to itself before reading any input.

This module owns only the process mechanics. Each extractor owns its argv,
its output contract, and the error types its callers see.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence


class BoundedProcessError(RuntimeError):
    """Base failure for one bounded child process run."""


class BoundedProcessSpawnError(BoundedProcessError):
    """The running event loop cannot spawn subprocesses."""


class BoundedProcessTimeoutError(BoundedProcessError):
    """The child was killed after exceeding its wall-clock deadline."""


class BoundedProcessOutputLimitError(BoundedProcessError):
    """A child pipe crossed the output byte ceiling while streaming."""

    def __init__(self, stream_name: str) -> None:
        super().__init__(f"child process exceeded the output byte limit on {stream_name}")
        self.stream_name = stream_name


class BoundedProcessExitError(BoundedProcessError):
    """The child exited with a non-zero status."""

    def __init__(self, returncode: int, detail: str) -> None:
        super().__init__(f"child process failed with exit code {returncode}: {detail}")
        self.returncode = returncode
        self.detail = detail


async def run_bounded_process(
    argv: Sequence[str],
    stdin_bytes: bytes,
    *,
    timeout_seconds: float,
    max_output_bytes: int,
) -> bytes:
    """Run ``argv`` with ``stdin_bytes`` on stdin and return its capped stdout.

    The caller bounds concurrency; this function bounds one run. It raises a
    :class:`BoundedProcessError` subclass for every failure mode so the caller
    can name the failure in its own domain terms.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError as error:
        # Trigger: the running loop cannot spawn subprocesses. Basic Memory
        # installs the selector loop on Windows for aiosqlite (see db.py),
        # and that loop has no subprocess transport.
        # Outcome: a named error instead of a bare NotImplementedError.
        raise BoundedProcessSpawnError(
            "child process needs an event loop with subprocess support; "
            "Basic Memory runs the selector loop on Windows"
        ) from error
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await _communicate_capped(process, stdin_bytes, cap=max_output_bytes)
    except asyncio.CancelledError:
        await kill_and_wait(process)
        raise
    except TimeoutError as error:
        await kill_and_wait(process)
        raise BoundedProcessTimeoutError(
            "child process exceeded the configured deadline"
        ) from error
    except BoundedProcessOutputLimitError:
        # A pipe crossed its ceiling while the child may still be writing.
        await kill_and_wait(process)
        raise

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[-2000:].strip()
        raise BoundedProcessExitError(
            process.returncode if process.returncode is not None else -1,
            detail or "no error detail",
        )
    return stdout


async def kill_and_wait(process: asyncio.subprocess.Process) -> None:
    """Terminate and reap one child before releasing worker capacity."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            # The child can exit between the returncode check and the signal.
            # Waiting still reaps it and preserves the caller's original outcome.
            pass
    await process.wait()


# Read pipes in modest chunks so the ceiling applies as bytes arrive rather than
# after an entire stream has already been buffered.
_PIPE_CHUNK_BYTES = 64 * 1024


async def _communicate_capped(
    process: asyncio.subprocess.Process,
    stdin_bytes: bytes,
    *,
    cap: int,
) -> tuple[bytes, bytes]:
    """Feed stdin and drain both pipes with a hard byte ceiling on each.

    ``Process.communicate()`` buffers stdout and stderr without limit before the
    caller can look at their size, so a hostile document that makes the worker
    emit a huge field, or a runaway error stream, would land in parent memory.
    This variant raises as soon as either pipe crosses ``cap``; the caller kills
    the child.
    """
    stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
    if stdin is None or stdout is None or stderr is None:  # pragma: no cover - PIPE at spawn
        raise RuntimeError("child process was started without pipes")
    try:
        async with asyncio.TaskGroup() as group:
            stdout_task = group.create_task(_read_capped(stdout, cap=cap, stream_name="stdout"))
            stderr_task = group.create_task(_read_capped(stderr, cap=cap, stream_name="stderr"))
            group.create_task(_feed_stdin(stdin, stdin_bytes))
    except* BoundedProcessOutputLimitError as overflow:
        raise overflow.exceptions[0] from None
    await process.wait()
    return stdout_task.result(), stderr_task.result()


async def _read_capped(stream: asyncio.StreamReader, *, cap: int, stream_name: str) -> bytes:
    buffer = bytearray()
    while chunk := await stream.read(_PIPE_CHUNK_BYTES):
        buffer.extend(chunk)
        if len(buffer) > cap:
            raise BoundedProcessOutputLimitError(stream_name)
    return bytes(buffer)


async def _feed_stdin(stdin: asyncio.StreamWriter, stdin_bytes: bytes) -> None:
    try:
        stdin.write(stdin_bytes)
        await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        # The child exited before consuming its input. Its exit status and
        # stderr carry the failure; ``Process.communicate()`` ignores the same
        # pair for the same reason.
        pass
    finally:
        stdin.close()
