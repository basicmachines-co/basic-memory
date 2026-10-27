"""Bug hunt regression test (#6): `bm tool read` exit code on a
path-traversal SECURITY_VALIDATION_ERROR.

The MCP read_note tool detects path-traversal identifiers and returns
{"error": "SECURITY_VALIDATION_ERROR", ...}. Every other wrapped tool command
exits non-zero on an error payload; read-note used to print the payload and
exit 0. These integration tests assert read-note now matches its siblings.
"""

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.tools.read import bm_read
from basic_memory.mcp.refusals import Refusal

runner = CliRunner()

TRAVERSAL_IDENTIFIER = "../../../../etc/passwd"


@pytest.mark.asyncio
async def test_read_note_security_error_mcp_emits_error_field(
    app, app_config, test_project, config_manager
):
    """Exact indexed reads refuse traversal-shaped identifiers without opening files."""
    with pytest.raises(Refusal) as caught:
        await bm_read(
            identifier=TRAVERSAL_IDENTIFIER, project=test_project.name, output_format="json"
        )
    assert caught.value.code == "NOT_FOUND"


def test_read_note_security_error_cli_exit_code_matches_other_tools(
    app, app_config, test_project, config_manager
):
    """CLI read-note must not exit 0 when the MCP payload carries an error."""
    result = runner.invoke(
        cli_app,
        ["tool", "read", TRAVERSAL_IDENTIFIER, "--project", test_project.name],
    )

    combined = result.stdout
    assert "SECURITY_VALIDATION_ERROR" in combined, combined

    assert result.exit_code != 0, (
        f"read-note exited {result.exit_code} on a SECURITY_VALIDATION_ERROR; "
        "other tool commands exit non-zero on error payloads"
    )
