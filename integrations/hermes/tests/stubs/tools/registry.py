"""Test-only stand-in for Hermes's tool registry helpers."""

from __future__ import annotations

import json


def tool_error(msg: str) -> str:
    return json.dumps({"error": str(msg)})
