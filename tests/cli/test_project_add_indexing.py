"""`bm project add` indexes, and readiness says so honestly (#1414).

The reported failure: `project add` registered a project without indexing it, so
every read surface reported an empty project while 25 notes sat on disk, and
`bm status` called that ready because nothing was pending -- nothing had ever
been queued.

These run the real CLI in a pristine HOME, the way `test_fresh_install.py` does.
The bug lived in the seam between the CLI, the API, and the module-level
database, and only a real install puts all three on the same database: the
in-process fixtures give the API an in-memory engine while the index runtime
opens its own from `db.get_or_create_db`, so an in-process test would prove
nothing about this path.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basic_memory.schemas.project_readiness import ProjectIndexPhase, ProjectIndexStageName

PROJECT_ROOT = Path(__file__).parent.parent.parent

NOTE_BODY = """---
title: {title}
type: note
---

# {title}

## Observations
- [fact] {title} was on disk before the project was added

## Relations
- relates_to [[{link}]]
"""


def _pristine_env(home: Path) -> dict[str, str]:
    """A profile with no Basic Memory state, and none inherited from the developer or CI."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("BASIC_MEMORY_") and key != "PYTEST_CURRENT_TEST"
    }
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        BASIC_MEMORY_HOME=str(home / "basic-memory"),
        BASIC_MEMORY_CONFIG_DIR=str(home / ".basic-memory"),
        BASIC_MEMORY_NO_PROMOS="1",
        # Rich falls back to 80 columns with no tty and would wrap the guidance
        # lines these tests match on. Pin a wide terminal so the assertions
        # describe the message rather than the runner's window.
        COLUMNS="240",
        LINES="60",
        # fastembed is a core dependency, so semantic search is on by default and
        # an index pass would download an embedding model onto the runner. These
        # tests are about index-on-add and the readiness phases, not embeddings;
        # the embeddings stage settles at 0/0 with this off, and the stage's own
        # counting is covered in tests/services/test_project_readiness.py.
        BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED="false",
    )
    return env


def _bm(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "bm", *args],
        capture_output=True,
        text=True,
        env=env,
        # Guards a wedged install, not a performance budget: the first run
        # creates the database and runs migrations.
        timeout=180,
        cwd=PROJECT_ROOT,
    )


def _seed_notes(root: Path) -> None:
    """Write two cross-linked notes, the way a directory being adopted looks."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "alpha.md").write_text(NOTE_BODY.format(title="Alpha Note", link="Beta Note"))
    (root / "beta.md").write_text(NOTE_BODY.format(title="Beta Note", link="Alpha Note"))


def _readiness(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Parse the readiness block out of `bm status --json` output.

    Log lines precede the JSON on stdout, so the parse starts at the first brace.
    """
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout[result.stdout.index("{") :])
    return payload["readiness"]


@pytest.mark.slow
def test_project_add_indexes_files_already_on_disk(tmp_path):
    """Notes present at add time are queryable with no manual reindex in between."""
    home = tmp_path / "home"
    home.mkdir()
    env = _pristine_env(home)
    notes = tmp_path / "adopted"
    _seed_notes(notes)

    add = _bm(["project", "add", "adopted", str(notes)], env)
    assert add.returncode == 0, add.stderr

    # No reindex is run here. That is the entire point of the test.
    search = _bm(["tool", "search-notes", "Alpha Note", "--project", "adopted", "--json"], env)

    assert search.returncode == 0, search.stderr
    assert "Alpha Note" in search.stdout


@pytest.mark.slow
def test_no_wait_leaves_a_never_indexed_project_that_status_reports_honestly(tmp_path):
    """--no-wait opts out, names the state, and `status --json` keeps it distinguishable.

    A never-indexed project and an idle one both have zero pending work. Before
    this change they were identical on the wire, which is how a waiter concluded
    an unindexed project was ready.
    """
    home = tmp_path / "home"
    home.mkdir()
    env = _pristine_env(home)
    notes = tmp_path / "deferred"
    _seed_notes(notes)

    add = _bm(["project", "add", "deferred", str(notes), "--no-wait"], env)
    assert add.returncode == 0, add.stderr
    assert "Skipped indexing" in add.stdout
    assert "bm project index deferred" in add.stdout
    assert "bm status --project deferred --json" in add.stdout
    # Names the state instead of showing silent emptiness.
    assert "2 files present, not yet indexed" in add.stdout

    never_indexed = _readiness(_bm(["status", "--project", "deferred", "--json"], env))
    assert never_indexed["phase"] == ProjectIndexPhase.NEVER_INDEXED
    assert never_indexed["last_indexed_at"] is None
    assert never_indexed["indexed_entities"] == 0
    # The files are visibly there; the project is unlooked-at, not empty.
    assert never_indexed["files_on_disk"] == 2
    assert all(
        stage["phase"] == ProjectIndexPhase.NEVER_INDEXED for stage in never_indexed["stages"]
    )

    index = _bm(["project", "index", "deferred"], env)
    assert index.returncode == 0, index.stderr

    idle = _readiness(_bm(["status", "--project", "deferred", "--json"], env))
    assert idle["phase"] == ProjectIndexPhase.IDLE
    assert idle["last_indexed_at"] is not None
    assert idle["indexed_entities"] == 2

    stages = {stage["name"]: stage for stage in idle["stages"]}
    never_indexed_stages = {stage["name"]: stage for stage in never_indexed["stages"]}
    assert stages[ProjectIndexStageName.FILES]["pending"] == 0

    # The demonstration that a pending count alone cannot carry this: relation
    # resolution reports zero outstanding work in BOTH states -- nothing owed
    # once indexed, and nothing ever queued before. Only the phase tells them
    # apart, and that is the bit that was missing.
    assert never_indexed_stages[ProjectIndexStageName.RELATIONS]["pending"] == 0
    assert stages[ProjectIndexStageName.RELATIONS]["pending"] == 0
    assert (
        never_indexed_stages[ProjectIndexStageName.RELATIONS]["phase"]
        != stages[ProjectIndexStageName.RELATIONS]["phase"]
    )

    # Relation resolution is its own settleable stage, and the cross-links
    # between the two seeded notes are resolved by the time it reports idle.
    relations = stages[ProjectIndexStageName.RELATIONS]
    assert relations["phase"] == ProjectIndexPhase.IDLE
    assert relations["total"] == 2

    # The file stage does move: two unindexed files on disk are outstanding work
    # before the pass, and none after it.
    assert never_indexed_stages[ProjectIndexStageName.FILES]["pending"] == 2
