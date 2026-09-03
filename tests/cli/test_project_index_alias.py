"""`bm project index` is an alias, not a second copy of reindex (#1414)."""

from typing import Any

from typer.testing import CliRunner

import basic_memory.cli.commands.project as project_cmd
from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_project_index_delegates_to_the_shared_reindex_implementation(monkeypatch):
    """It supplies flags to `run_reindex_command`; it does not restate the logic.

    Embeddings are included on purpose. This is the command that `project add`
    and `bm status` print as the remedy for a not-yet-indexed project, so it has
    to reach the ready state they promise -- a search-only pass would leave the
    embeddings stage pending and the printed advice self-defeating.
    """
    calls: list[dict[str, Any]] = []

    def fake_run_reindex_command(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(project_cmd, "run_reindex_command", fake_run_reindex_command)

    result = runner.invoke(cli_app, ["project", "index", "research"])

    assert result.exit_code == 0, result.output
    assert calls == [{"embeddings": True, "search": True, "full": True, "project": "research"}]
