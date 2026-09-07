"""Named manifest entry point for Tau's extension list.

Tau derives the display name from this filename, not project.name.
Keep the implementation importable separately for tests and existing callers.
"""

# Tau loads manifest entries as packages, including human-readable filenames.
from .extension import setup  # ty: ignore[unresolved-import]

__all__ = ["setup"]
