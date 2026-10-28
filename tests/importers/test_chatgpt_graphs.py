"""Bound malformed ChatGPT graphs at the real import boundary."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("cycle", ["root", "message", "empty", "shared"])
def test_chatgpt_import_visits_each_mapping_key_once(tmp_path: Path, cycle: str) -> None:
    message = {
        "id": "message",
        "author": {"role": "user"},
        "content": {"content_type": "text", "parts": ["Visible message"]},
        "metadata": {},
    }
    mapping = {
        "root": {"id": "root", "parent": None, "message": None, "children": ["message"]},
        "message": {"id": "message", "parent": "root", "message": message, "children": []},
        "empty": {"id": "empty", "parent": "message", "message": None, "children": []},
    }
    match cycle:
        case "root":
            mapping["root"]["children"] = ["root", "message"]
        case "message":
            mapping["message"]["children"] = ["message"]
        case "empty":
            mapping["message"]["children"] = ["empty"]
            mapping["empty"]["children"] = ["empty"]
        case "shared":
            # Embedded IDs are not graph identities: exports link by mapping key.
            mapping["empty"]["id"] = "message"
            mapping["root"]["children"] = ["empty", "message", "message"]
            mapping["empty"]["children"] = ["message"]
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps([{"title": "Graph", "create_time": 86400, "mapping": mapping}]),
        encoding="utf-8",
    )
    # A separate process bounds synchronous infinite loops without stranding pytest's
    # event loop or leaving an import worker running after the regression fails.
    result = subprocess.run(
        [sys.executable, "-m", "basic_memory.cli.main", "import", "chatgpt", str(export)],
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    notes = list(tmp_path.rglob("*-Graph.md"))
    assert len(notes) == 1, result.stdout + result.stderr
    assert notes[0].read_text(encoding="utf-8").count("Visible message") == 1
