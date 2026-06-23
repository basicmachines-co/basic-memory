"""Portable runtime process measurements."""

import sys
from pathlib import Path
from types import ModuleType

import psutil

try:
    import resource as _resource
except ModuleNotFoundError:  # pragma: no cover - exercised by Windows CI.
    resource: ModuleType | None = None
else:
    resource = _resource


def runtime_process_rss_bytes(
    *,
    proc_status_path: Path = Path("/proc/self/status"),
    platform_name: str = sys.platform,
) -> int:
    """Return the current process RSS in bytes using the best local source."""
    try:
        if proc_status_path.exists():
            for line in proc_status_path.read_text().splitlines():
                if not line.startswith("VmRSS:"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) * 1024
    except OSError:
        pass

    if resource is not None:
        rss_units = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if platform_name == "darwin":
            return rss_units
        return rss_units * 1024

    if psutil is None:
        return 0

    return int(psutil.Process().memory_info().rss)
