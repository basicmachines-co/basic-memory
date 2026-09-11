"""POSIX resource limits an extractor worker process applies to itself.

Every worker runs a parser over untrusted bytes, so it caps its own CPU time and
(on Linux) address space before reading a single byte of input. The parent's
wall-clock deadline in ``bounded_process`` is the other half of the boundary.
"""

from __future__ import annotations

import sys

# POSIX rlimits are the child's isolation boundary on the Linux workers and on
# macOS dev machines. Windows has no `resource` module; there the parent's
# wall-clock deadline is the only ceiling, and the helpers below no-op.
if sys.platform == "win32":  # pragma: no cover - exercised only on Windows CI
    resource = None
else:
    import resource


def apply_cpu_limit(cpu_seconds: int) -> None:
    """Bound CPU time so a hostile document cannot monopolize a worker."""
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))


def apply_memory_limit(max_memory_bytes: int) -> None:
    """Bound parser address space on Linux before reading source bytes."""
    if resource is None or sys.platform != "linux":
        # RLIMIT_AS is the deployed Linux isolation boundary. Applying the same
        # byte ceiling on macOS constrains its much larger virtual mappings and
        # makes the local subprocess fail before parsing begins.
        return
    resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
