"""Public share CLI commands for Basic Memory Cloud.

Surfaces the cloud `/api/shares` endpoints so users can manage public share
links for notes without leaving the terminal:

- POST   /api/shares          -> create
- GET    /api/shares          -> list
- PATCH  /api/shares/{token}   -> update (enable/disable, set expiration)
- DELETE /api/shares/{token}   -> revoke

Auth, config lookup, and error handling reuse the shared `make_api_request()`
helper, matching the `snapshot.py` command group.
"""

import asyncio
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
    make_api_request,
)
from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import resolve_configured_workspace
from basic_memory.schemas.cloud import WorkspaceInfo

console = Console()
share_app = typer.Typer(help="Manage public share links for notes")

# Header the cloud uses to route a request to a specific tenant's workspace.
# Mirrors basic_memory.cli.commands.cloud.cloud_utils._workspace_headers: the
# cloud /api/shares endpoints resolve the workspace from X-Workspace-ID (see
# resolve_workspace in basic-memory-cloud deps.py), so without it a team
# workspace project would be evaluated against the caller's default tenant.
WORKSPACE_ID_HEADER = "X-Workspace-ID"


def _is_uuid(value: str) -> bool:
    """Return True when value parses as a UUID in any standard textual form."""
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _match_workspace_identifier(
    workspaces: list[WorkspaceInfo], identifier: str
) -> Optional[WorkspaceInfo]:
    """Match a human workspace identifier with slug > tenant_id > name precedence.

    Mirrors project_context._match_workspace_identifier (PR #979): try the stable
    slug first (case-insensitive), then the exact tenant_id, then the display name
    (case-insensitive). The first tier that yields any match wins, so a display
    name colliding with another workspace's slug never shadows the slug match.
    """
    slug_matches = [ws for ws in workspaces if ws.slug.casefold() == identifier.casefold()]
    if slug_matches:
        return slug_matches[0] if len(slug_matches) == 1 else _ambiguous(slug_matches, identifier)

    tenant_matches = [ws for ws in workspaces if ws.tenant_id == identifier]
    if tenant_matches:
        return tenant_matches[0]

    name_matches = [ws for ws in workspaces if ws.name.casefold() == identifier.casefold()]
    if name_matches:
        return name_matches[0] if len(name_matches) == 1 else _ambiguous(name_matches, identifier)

    return None


def _ambiguous(matches: list[WorkspaceInfo], identifier: str) -> WorkspaceInfo:
    """Fail with a clear, copyable error when an identifier matches >1 workspace.

    Trigger: a display name (or slug, defensively) resolves to multiple workspaces.
    Why: silently picking one would route a share to the wrong tenant.
    Outcome: list candidate slugs/tenant_ids and exit non-zero so the user re-runs
        with an unambiguous slug or tenant_id.
    """
    candidates = "\n".join(f"  - {ws.slug} (tenant_id: {ws.tenant_id})" for ws in matches)
    console.print(
        f"[red]Workspace '{identifier}' is ambiguous; it matches multiple workspaces.[/red]\n"
        "[yellow]Re-run with a unique workspace slug or tenant_id:[/yellow]\n"
        f"{candidates}"
    )
    raise typer.Exit(1)


async def _resolve_workspace_to_tenant_id(identifier: str) -> str:
    """Resolve a human workspace identifier (slug/name/tenant_id) to a tenant UUID.

    Constraint: the cloud's X-Workspace-ID resolver only accepts a workspace/tenant
    UUID, but users see slugs and display names (in list-workspaces output and
    memory:// URLs). So when --workspace (or the configured default) is not already
    a UUID, fetch the caller's workspaces once and map it to the tenant_id here.

    get_available_workspaces is the same workspace-fetch seam project.py uses for
    CLI workspace resolution; it is awaited directly here because the share
    commands already run inside their own event loop.
    """
    from basic_memory.mcp.project_context import get_available_workspaces

    workspaces = await get_available_workspaces()
    match = _match_workspace_identifier(workspaces, identifier)
    if match is None:
        available = "\n".join(f"  - {ws.slug}" for ws in workspaces)
        console.print(
            f"[red]Workspace '{identifier}' was not found.[/red]\n"
            "[yellow]Use one of these workspace slugs (or a tenant_id):[/yellow]\n"
            f"{available}"
        )
        raise typer.Exit(1)
    return match.tenant_id


async def _workspace_headers(
    *,
    project_name: Optional[str] = None,
    workspace: Optional[str] = None,
) -> dict[str, str]:
    """Resolve the target workspace and build the routing header, if any.

    Resolution chain (see resolve_configured_workspace): explicit --workspace,
    then the project's configured workspace_id, then the global default. Returns
    an empty dict when nothing resolves so the request falls back to the
    caller's default tenant exactly as before.

    The cloud's X-Workspace-ID resolver only accepts a workspace/tenant UUID. A
    UUID is forwarded verbatim (covers per-project config workspace_id values and
    the default chain, zero extra API calls); any other value is treated as a
    human identifier and resolved to the tenant UUID via one workspace lookup.
    """
    resolved = resolve_configured_workspace(project_name=project_name, workspace=workspace)
    if resolved is None:
        return {}
    if _is_uuid(resolved):
        return {WORKSPACE_ID_HEADER: resolved}
    tenant_id = await _resolve_workspace_to_tenant_id(resolved)
    return {WORKSPACE_ID_HEADER: tenant_id}


def _format_timestamp(iso_timestamp: Optional[str]) -> str:
    """Format an ISO timestamp to a human-readable form, or '-' when absent."""
    if not iso_timestamp:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return iso_timestamp


def _parse_expires_at(value: str) -> str:
    """Validate an --expires-at value and normalize it to an ISO 8601 string.

    Accepts either a full ISO timestamp ("2025-12-31T23:59:00") or a bare date
    ("2025-12-31"). Exits with a clear error on anything we can't parse so the
    server never sees a malformed payload.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        console.print(
            f"[red]Invalid --expires-at value '{value}'. "
            "Use ISO format, e.g. 2025-12-31 or 2025-12-31T23:59:00.[/red]"
        )
        raise typer.Exit(1)
    return dt.isoformat()


def _print_share_details(data: dict) -> None:
    """Print a single share's fields in the snapshot-style detail layout."""
    console.print(f"  Token: {data.get('token', 'unknown')}")
    console.print(f"  URL: [blue underline]{data.get('share_url', '-')}[/blue underline]")
    console.print(f"  Project: {data.get('project_name', '-')}")
    console.print(f"  Note: {data.get('note_permalink', '-')}")
    console.print(f"  Enabled: {'yes' if data.get('enabled', False) else 'no'}")
    console.print(f"  Expires: {_format_timestamp(data.get('expires_at'))}")
    console.print(f"  Views: {data.get('view_count', 0)}")
    console.print(f"  Created: {_format_timestamp(data.get('created_at'))}")


@share_app.command("create")
def create(
    project: str = typer.Argument(
        ...,
        help="Name of the project the note belongs to",
    ),
    permalink: str = typer.Argument(
        ...,
        help="Permalink of the note to share",
    ),
    expires_at: Optional[str] = typer.Option(
        None,
        "--expires-at",
        "-e",
        help="Optional expiration date/time (ISO 8601, e.g. 2025-12-31)",
    ),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        help="Workspace to route to: a workspace slug, display name, or tenant ID",
    ),
) -> None:
    """Create a public share link for a note.

    Examples:
      bm cloud share create my-project notes/my-idea
      bm cloud share create my-project notes/my-idea --expires-at 2025-12-31
      bm cloud share create my-project notes/my-idea --workspace acme
    """

    # Validate --expires-at before any async/API work so a parse error surfaces
    # a single clean message and exits, rather than being re-wrapped by the broad
    # handler below as "Unexpected error: 1" (typer.Exit subclasses Exception).
    payload: dict = {
        "project_name": project,
        "note_permalink": permalink,
    }
    if expires_at is not None:
        payload["expires_at"] = _parse_expires_at(expires_at)

    async def _create():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            console.print("[blue]Creating share link...[/blue]")

            response = await make_api_request(
                method="POST",
                url=f"{host_url}/api/shares",
                json_data=payload,
                headers=await _workspace_headers(project_name=project, workspace=workspace),
            )

            data = response.json()

            console.print("[green]Share link created successfully[/green]")
            _print_share_details(data)

        except typer.Exit:
            raise
        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            if e.status_code == 404:
                console.print(f"[red]Note not found: {permalink} (project: {project})[/red]")
            else:
                console.print(f"[red]Failed to create share link: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_create())


@share_app.command("list")
def list_shares(
    project: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Filter shares by project name",
    ),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        help="Workspace to route to: a workspace slug, display name, or tenant ID",
    ),
) -> None:
    """List public share links.

    Examples:
      bm cloud share list
      bm cloud share list --project my-project
      bm cloud share list --project my-project --workspace acme
    """

    async def _list():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            url = f"{host_url}/api/shares"
            if project:
                # Encode the filter so project names with query-reserved
                # characters (&, +, #, spaces) reach the server intact rather
                # than being parsed as extra query parameters.
                url += f"?{urlencode({'project_name': project})}"

            console.print("[blue]Fetching share links...[/blue]")

            response = await make_api_request(
                method="GET",
                url=url,
                headers=await _workspace_headers(project_name=project, workspace=workspace),
            )

            data = response.json()
            shares = data.get("shares", [])
            total = data.get("total", len(shares))

            if not shares:
                console.print("[yellow]No share links found[/yellow]")
                console.print(
                    "\n[dim]Create a share with: bm cloud share create <project> <permalink>[/dim]"
                )
                return

            table = Table(title=f"Public Shares ({total} total)")
            table.add_column("Token", style="cyan", no_wrap=True)
            table.add_column("Project", style="yellow")
            table.add_column("Note", style="white")
            table.add_column("Enabled", style="green")
            table.add_column("Expires", style="green")
            table.add_column("Views", style="magenta", justify="right")
            table.add_column("URL", style="blue", overflow="fold")

            for share in shares:
                table.add_row(
                    share.get("token", "unknown"),
                    share.get("project_name", "-"),
                    share.get("note_permalink", "-"),
                    "yes" if share.get("enabled", False) else "no",
                    _format_timestamp(share.get("expires_at")),
                    str(share.get("view_count", 0)),
                    share.get("share_url", "-"),
                )

            console.print(table)

        # Re-raise typer.Exit before the broad handler below: workspace resolution
        # raises typer.Exit (a subclass of Exception) for ambiguous/unknown
        # identifiers, and that must not be re-wrapped as "Unexpected error: 1".
        except typer.Exit:
            raise
        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            console.print(f"[red]Failed to list share links: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_list())


@share_app.command("update")
def update(
    token: str = typer.Argument(
        ...,
        help="The token of the share to update",
    ),
    enable: bool = typer.Option(
        False,
        "--enable",
        help="Enable the share link",
    ),
    disable: bool = typer.Option(
        False,
        "--disable",
        help="Disable the share link without deleting it",
    ),
    expires_at: Optional[str] = typer.Option(
        None,
        "--expires-at",
        "-e",
        help="New expiration date/time (ISO 8601). Use 'none' to clear it.",
    ),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        help="Workspace the share belongs to: a workspace slug, display name, or tenant ID",
    ),
) -> None:
    """Update a share link: enable/disable it or change its expiration.

    Examples:
      bm cloud share update abc123 --disable
      bm cloud share update abc123 --enable
      bm cloud share update abc123 --expires-at 2026-01-01
      bm cloud share update abc123 --expires-at none
      bm cloud share update abc123 --disable --workspace acme
    """

    async def _update():
        try:
            # --- Validate flags ---
            # Trigger: both toggles passed, or neither toggle and no expiry change.
            # Why: PATCH needs at least one concrete field, and enable/disable
            #      conflict; reject up front so we don't send an empty/ambiguous body.
            if enable and disable:
                console.print("[red]Cannot use --enable and --disable together[/red]")
                raise typer.Exit(1)
            if not enable and not disable and expires_at is None:
                console.print(
                    "[red]Nothing to update. Pass --enable, --disable, or --expires-at.[/red]"
                )
                raise typer.Exit(1)

            payload: dict = {}
            if enable:
                payload["enabled"] = True
            if disable:
                payload["enabled"] = False
            if expires_at is not None:
                # "none" clears the expiration; anything else is parsed as a date.
                payload["expires_at"] = (
                    None if expires_at.lower() == "none" else _parse_expires_at(expires_at)
                )

            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            console.print("[blue]Updating share link...[/blue]")

            response = await make_api_request(
                method="PATCH",
                url=f"{host_url}/api/shares/{token}",
                json_data=payload,
                headers=await _workspace_headers(workspace=workspace),
            )

            data = response.json()

            console.print("[green]Share link updated successfully[/green]")
            _print_share_details(data)

        except typer.Exit:
            raise
        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            if e.status_code == 404:
                console.print(f"[red]Share not found: {token}[/red]")
            else:
                console.print(f"[red]Failed to update share link: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_update())


@share_app.command("revoke")
def revoke(
    token: str = typer.Argument(
        ...,
        help="The token of the share to revoke",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt",
    ),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        help="Workspace the share belongs to: a workspace slug, display name, or tenant ID",
    ),
) -> None:
    """Revoke (delete) a public share link.

    Examples:
      bm cloud share revoke abc123
      bm cloud share revoke abc123 --force
      bm cloud share revoke abc123 --force --workspace acme
    """

    async def _revoke():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            if not force:
                confirmed = typer.confirm(f"Are you sure you want to revoke share '{token}'?")
                if not confirmed:
                    console.print("[yellow]Revocation cancelled[/yellow]")
                    raise typer.Exit(0)

            console.print("[blue]Revoking share link...[/blue]")

            await make_api_request(
                method="DELETE",
                url=f"{host_url}/api/shares/{token}",
                headers=await _workspace_headers(workspace=workspace),
            )

            console.print(f"[green]Share {token} revoked successfully[/green]")

        except typer.Exit:
            raise
        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            if e.status_code == 404:
                console.print(f"[red]Share not found: {token}[/red]")
            else:
                console.print(f"[red]Failed to revoke share link: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_revoke())
