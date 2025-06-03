"""basic-memory - Local-first knowledge management combining Zettelkasten with knowledge graphs"""

try:
    from importlib.metadata import version

    __version__ = version("basic-memory")
except Exception:  # pragma: no cover
    # Fallback if package not installed (e.g., during development)
    __version__ = "0.0.0"  # pragma: no cover
