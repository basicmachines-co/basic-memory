"""Helpers for serving MCP UI HTML resources."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_VARIANT = "vanilla"
SUPPORTED_VARIANTS = {"vanilla", "tool-ui", "mcp-ui"}


def get_ui_variant() -> str:
    """Return the active UI variant from environment settings."""
    value = os.getenv("BASIC_MEMORY_MCP_UI_VARIANT", DEFAULT_VARIANT).strip().lower()
    return value if value in SUPPORTED_VARIANTS else DEFAULT_VARIANT


def load_html(filename: str) -> str:
    """Load a UI HTML template from disk."""
    path = Path(__file__).parent / "html" / filename
    return path.read_text(encoding="utf-8")


def load_variant_html(base_name: str) -> str:
    """Load a UI template for the current variant."""
    variant = get_ui_variant()
    return load_html(f"{base_name}-{variant}.html")
