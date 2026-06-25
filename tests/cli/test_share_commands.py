"""Tests for cloud share CLI commands.

Issue #880: Tests for share create, list, update, revoke commands that surface
the cloud /api/shares endpoints.
"""

from unittest.mock import AsyncMock, Mock, patch

import httpx
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
)
from basic_memory.schemas.cloud import WorkspaceInfo

# A real workspace/tenant UUID is forwarded verbatim as X-Workspace-ID with no
# workspace lookup; the cloud's resolver only accepts this UUID form.
TENANT_UUID = "5ccbae40-ca03-43a2-b23d-9931eb130e22"


def _workspace(slug: str, tenant_id: str, name: str) -> WorkspaceInfo:
    """Build a WorkspaceInfo for workspace-resolution tests."""
    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type="organization",
        slug=slug,
        name=name,
        role="owner",
        is_default=False,
    )


def _patch_available_workspaces(workspaces):
    """Patch the workspace list fetch used when --workspace is a slug/name.

    Asserts can wrap the returned mock to confirm the lookup was (or was not)
    performed for a given invocation.
    """
    return patch(
        "basic_memory.mcp.project_context.get_available_workspaces",
        new=AsyncMock(return_value=workspaces),
    )


SHARE_RESPONSE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "token": "abc123",
    "project_name": "my-project",
    "note_permalink": "notes/my-idea",
    "note_external_id": "ext-1",
    "enabled": True,
    "expires_at": None,
    "share_url": "https://share.example.com/abc123",
    "view_count": 0,
    "last_viewed_at": None,
    "created_at": "2025-01-18T12:00:00Z",
}


def _mock_config_manager():
    mock_config = Mock()
    mock_config.cloud_host = "https://cloud.example.com"
    mock_config_manager = Mock()
    mock_config_manager.config = mock_config
    return mock_config_manager


def _patch_workspace(resolved):
    """Patch the workspace resolver used by the share commands.

    Returns whatever ``resolved`` is for every lookup, so tests can assert the
    X-Workspace-ID header is built (and routed) the way the cloud expects
    without depending on real config files.
    """
    return patch(
        "basic_memory.cli.commands.cloud.shares.resolve_configured_workspace",
        return_value=resolved,
    )


class TestShareCreateCommand:
    """Tests for 'bm cloud share create' command."""

    def test_create_share_success(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            captured["headers"] = kwargs.get("headers")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace(TENANT_UUID):
                    with _patch_available_workspaces([]) as fetch:
                        result = runner.invoke(
                            app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                        )

                assert result.exit_code == 0
                assert "Share link created successfully" in result.stdout
                assert "abc123" in result.stdout
                assert "https://share.example.com/abc123" in result.stdout
                # Payload should match the cloud CreateShareRequest contract.
                assert captured["json_data"] == {
                    "project_name": "my-project",
                    "note_permalink": "notes/my-idea",
                }
                # Workspace routing: a resolved tenant UUID travels verbatim as the
                # X-Workspace-ID header so team-workspace projects aren't evaluated
                # against the caller's default tenant.
                assert captured["headers"] == {"X-Workspace-ID": TENANT_UUID}
                # A UUID needs no resolution: the workspace list is never fetched.
                fetch.assert_not_called()

    def test_create_share_slug_resolves_to_tenant_uuid(self):
        """A --workspace slug is resolved to the tenant UUID before routing."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return mock_response

        seen = {}

        def fake_resolve(*, project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            return workspace

        workspaces = [
            _workspace("basic-memory-7020de4e925843c68c9056c60d101d9e", TENANT_UUID, "Acme Org"),
            _workspace("other-slug", "11111111-1111-1111-1111-111111111111", "Other"),
        ]

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with patch(
                    "basic_memory.cli.commands.cloud.shares.resolve_configured_workspace",
                    side_effect=fake_resolve,
                ):
                    with _patch_available_workspaces(workspaces) as fetch:
                        result = runner.invoke(
                            app,
                            [
                                "cloud",
                                "share",
                                "create",
                                "my-project",
                                "notes/my-idea",
                                "--workspace",
                                "basic-memory-7020de4e925843c68c9056c60d101d9e",
                            ],
                        )

                assert result.exit_code == 0
                assert seen == {
                    "project_name": "my-project",
                    "workspace": "basic-memory-7020de4e925843c68c9056c60d101d9e",
                }
                # The slug was mapped to the workspace's tenant UUID.
                assert captured["headers"] == {"X-Workspace-ID": TENANT_UUID}
                fetch.assert_awaited_once()

    def test_create_share_display_name_resolves_case_insensitively(self):
        """A --workspace display name resolves case-insensitively to the tenant UUID."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return mock_response

        workspaces = [_workspace("acme-slug", TENANT_UUID, "Acme Org")]

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace("acme org"):
                    with _patch_available_workspaces(workspaces):
                        result = runner.invoke(
                            app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                        )

                assert result.exit_code == 0
                assert captured["headers"] == {"X-Workspace-ID": TENANT_UUID}

    def test_create_share_tenant_id_input_passthrough(self):
        """A tenant UUID resolved from config is forwarded without a lookup."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace(TENANT_UUID):
                    with _patch_available_workspaces([]) as fetch:
                        result = runner.invoke(
                            app,
                            [
                                "cloud",
                                "share",
                                "create",
                                "my-project",
                                "notes/my-idea",
                                "--workspace",
                                TENANT_UUID,
                            ],
                        )

                assert result.exit_code == 0
                assert captured["headers"] == {"X-Workspace-ID": TENANT_UUID}
                fetch.assert_not_called()

    def test_create_share_ambiguous_name_errors_with_candidate_slugs(self):
        """A display name matching multiple workspaces errors and lists candidates."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called when workspace is ambiguous")

        workspaces = [
            _workspace("acme-prod", TENANT_UUID, "Acme"),
            _workspace("acme-staging", "11111111-1111-1111-1111-111111111111", "Acme"),
        ]

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace("Acme"):
                    with _patch_available_workspaces(workspaces):
                        result = runner.invoke(
                            app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                        )

                assert result.exit_code == 1
                assert "ambiguous" in result.stdout
                # Candidate slugs are listed so the user can disambiguate.
                assert "acme-prod" in result.stdout
                assert "acme-staging" in result.stdout
                # The typer.Exit must not be re-wrapped by the broad handler.
                assert "Unexpected error" not in result.stdout

    def test_create_share_unknown_workspace_errors_with_available_slugs(self):
        """An unknown identifier errors and lists the available workspace slugs."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called for an unknown workspace")

        workspaces = [
            _workspace("acme-prod", TENANT_UUID, "Acme"),
            _workspace("widget-co", "11111111-1111-1111-1111-111111111111", "Widget Co"),
        ]

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace("does-not-exist"):
                    with _patch_available_workspaces(workspaces):
                        result = runner.invoke(
                            app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                        )

                assert result.exit_code == 1
                assert "was not found" in result.stdout
                assert "acme-prod" in result.stdout
                assert "widget-co" in result.stdout
                assert "Unexpected error" not in result.stdout

    def test_create_share_no_workspace_sends_no_header(self):
        """When nothing resolves, no routing header is added (default tenant)."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace(None):
                    result = runner.invoke(
                        app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                    )

                assert result.exit_code == 0
                assert captured["headers"] == {}

    def test_create_share_with_expires_at(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "share",
                        "create",
                        "my-project",
                        "notes/my-idea",
                        "--expires-at",
                        "2025-12-31",
                    ],
                )

                assert result.exit_code == 0
                assert captured["json_data"]["expires_at"].startswith("2025-12-31")

    def test_create_share_invalid_expires_at(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called on invalid input")

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "share",
                        "create",
                        "my-project",
                        "notes/my-idea",
                        "--expires-at",
                        "not-a-date",
                    ],
                )

                assert result.exit_code == 1
                assert "Invalid --expires-at" in result.stdout
                # A parse error must produce a single clean message, not a
                # spurious "Unexpected error: 1" from the broad handler
                # re-catching typer.Exit. See issue #880 review.
                assert "Unexpected error" not in result.stdout

    def test_create_share_note_not_found(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "create", "my-project", "notes/missing"]
                )

                assert result.exit_code == 1
                assert "Note not found" in result.stdout

    def test_create_share_subscription_required(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise SubscriptionRequiredError(
                message="Active subscription required",
                subscribe_url="https://basicmemory.com/subscribe",
            )

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                )

                assert result.exit_code == 1
                assert "Subscription Required" in result.stdout

    def test_create_share_api_error(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Server error", status_code=500)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                )

                assert result.exit_code == 1
                assert "Failed to create share link" in result.stdout


class TestShareListCommand:
    """Tests for 'bm cloud share list' command."""

    def test_list_shares_success(self):
        # Wide terminal so the rich table doesn't truncate cell contents.
        runner = CliRunner(env={"COLUMNS": "200"})

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "shares": [
                SHARE_RESPONSE,
                {
                    **SHARE_RESPONSE,
                    "token": "def456",
                    "note_permalink": "notes/second",
                    "enabled": False,
                    "expires_at": "2025-12-31T00:00:00Z",
                    "view_count": 7,
                },
            ],
            "total": 2,
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "list"])

                assert result.exit_code == 0
                assert "abc123" in result.stdout
                assert "def456" in result.stdout
                assert "notes/second" in result.stdout

    def test_list_shares_empty(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"shares": [], "total": 0}

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "list"])

                assert result.exit_code == 0
                assert "No share links found" in result.stdout

    def test_list_shares_with_project_filter(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"shares": [SHARE_RESPONSE], "total": 1}

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["url"] = kwargs.get("url", args[1] if len(args) > 1 else "")
            captured["headers"] = kwargs.get("headers")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace(TENANT_UUID):
                    result = runner.invoke(
                        app, ["cloud", "share", "list", "--project", "my-project"]
                    )

                assert result.exit_code == 0
                assert "project_name=my-project" in captured["url"]
                assert captured["headers"] == {"X-Workspace-ID": TENANT_UUID}

    def test_list_shares_unknown_workspace_errors_without_double_error(self):
        """An unknown --workspace on list errors cleanly (no 'Unexpected error').

        Exercises the list handler's typer.Exit re-raise: workspace resolution
        raises typer.Exit, which must not be re-wrapped by the broad handler.
        """
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called for an unknown workspace")

        workspaces = [_workspace("acme-prod", TENANT_UUID, "Acme")]

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace("does-not-exist"):
                    with _patch_available_workspaces(workspaces):
                        result = runner.invoke(app, ["cloud", "share", "list"])

                assert result.exit_code == 1
                assert "was not found" in result.stdout
                assert "acme-prod" in result.stdout
                assert "Unexpected error" not in result.stdout

    def test_list_shares_ambiguous_slug_errors_with_candidates(self):
        """A slug colliding across workspaces errors and lists candidates.

        Exercises the slug tier of _match_workspace_identifier raising on >1 match.
        """
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called when the slug is ambiguous")

        workspaces = [
            _workspace("shared-slug", TENANT_UUID, "Acme"),
            _workspace("shared-slug", "11111111-1111-1111-1111-111111111111", "Widget"),
        ]

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace("shared-slug"):
                    with _patch_available_workspaces(workspaces):
                        result = runner.invoke(app, ["cloud", "share", "list"])

                assert result.exit_code == 1
                assert "ambiguous" in result.stdout
                assert TENANT_UUID in result.stdout
                assert "Unexpected error" not in result.stdout

    def test_list_shares_project_filter_url_encoded(self):
        """Project names with query-reserved chars must be percent-encoded.

        A name like "R&D+notes #1" interpolated raw would split into bogus
        query params (project_name=R, plus a stray "D+notes #1" key); encoding
        keeps it a single faithful project_name value.
        """
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"shares": [SHARE_RESPONSE], "total": 1}

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["url"] = kwargs.get("url", args[1] if len(args) > 1 else "")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace(None):
                    result = runner.invoke(
                        app, ["cloud", "share", "list", "--project", "R&D+notes #1"]
                    )

                assert result.exit_code == 0
                # Reserved characters are percent-encoded into a single value.
                assert "project_name=R%26D%2Bnotes+%231" in captured["url"]
                # And the raw, ambiguous form never reaches the wire.
                assert "project_name=R&D" not in captured["url"]

    def test_list_shares_api_error(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Server error", status_code=500)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "list"])

                assert result.exit_code == 1
                assert "Failed to list share links" in result.stdout


class TestShareUpdateCommand:
    """Tests for 'bm cloud share update' command."""

    def test_update_disable(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {**SHARE_RESPONSE, "enabled": False}

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            captured["method"] = kwargs.get("method")
            captured["headers"] = kwargs.get("headers")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace(TENANT_UUID):
                    result = runner.invoke(
                        app,
                        ["cloud", "share", "update", "abc123", "--disable", "--workspace", "acme"],
                    )

                assert result.exit_code == 0
                assert "updated successfully" in result.stdout
                assert captured["method"] == "PATCH"
                assert captured["json_data"] == {"enabled": False}
                assert captured["headers"] == {"X-Workspace-ID": TENANT_UUID}

    def test_update_enable(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "update", "abc123", "--enable"])

                assert result.exit_code == 0
                assert captured["json_data"] == {"enabled": True}

    def test_update_expires_at(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app,
                    ["cloud", "share", "update", "abc123", "--expires-at", "2026-01-01"],
                )

                assert result.exit_code == 0
                assert captured["json_data"]["expires_at"].startswith("2026-01-01")

    def test_update_clear_expires_at(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "update", "abc123", "--expires-at", "none"]
                )

                assert result.exit_code == 0
                assert captured["json_data"] == {"expires_at": None}

    def test_update_enable_and_disable_conflict(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called on conflicting flags")

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app,
                    ["cloud", "share", "update", "abc123", "--enable", "--disable"],
                )

                assert result.exit_code == 1
                assert "Cannot use --enable and --disable together" in result.stdout

    def test_update_nothing_to_change(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called with empty update")

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "update", "abc123"])

                assert result.exit_code == 1
                assert "Nothing to update" in result.stdout

    def test_update_not_found(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "update", "missing", "--disable"])

                assert result.exit_code == 1
                assert "Share not found" in result.stdout


class TestShareRevokeCommand:
    """Tests for 'bm cloud share revoke' command."""

    def test_revoke_success_with_force(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 204
        mock_response.json.return_value = {}

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["method"] = kwargs.get("method")
            captured["url"] = kwargs.get("url")
            captured["headers"] = kwargs.get("headers")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                with _patch_workspace(TENANT_UUID):
                    result = runner.invoke(
                        app,
                        ["cloud", "share", "revoke", "abc123", "--force", "--workspace", "acme"],
                    )

                assert result.exit_code == 0
                assert "revoked successfully" in result.stdout
                assert captured["method"] == "DELETE"
                assert captured["url"].endswith("/api/shares/abc123")
                assert captured["headers"] == {"X-Workspace-ID": TENANT_UUID}

    def test_revoke_cancelled(self):
        runner = CliRunner()

        call_count = 0

        async def mock_make_api_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return Mock(spec=httpx.Response)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "revoke", "abc123"], input="n\n")

                assert result.exit_code == 0
                assert "cancelled" in result.stdout
                assert call_count == 0

    def test_revoke_not_found(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "revoke", "missing", "--force"])

                assert result.exit_code == 1
                assert "Share not found" in result.stdout

    def test_revoke_subscription_required(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise SubscriptionRequiredError(
                message="Active subscription required",
                subscribe_url="https://basicmemory.com/subscribe",
            )

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "revoke", "abc123", "--force"])

                assert result.exit_code == 1
                assert "Subscription Required" in result.stdout
