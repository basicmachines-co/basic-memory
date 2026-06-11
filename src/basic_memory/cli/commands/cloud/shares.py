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

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
    make_api_request,
)
from basic_memory.config import ConfigManager

console = Console()
share_app = typer.Typer(help="Manage public share links for notes")


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
) -> None:
    """Create a public share link for a note.

    Examples:
      bm cloud share create my-project notes/my-idea
      bm cloud share create my-project notes/my-idea --expires-at 2025-12-31
    """

    async def _create():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            payload: dict = {
                "project_name": project,
                "note_permalink": permalink,
            }
            if expires_at is not None:
                payload["expires_at"] = _parse_expires_at(expires_at)

            console.print("[blue]Creating share link...[/blue]")

            response = await make_api_request(
                method="POST",
                url=f"{host_url}/api/shares",
                json_data=payload,
            )

            data = response.json()

            console.print("[green]Share link created successfully[/green]")
            _print_share_details(data)

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
) -> None:
    """List public share links.

    Examples:
      bm cloud share list
      bm cloud share list --project my-project
    """

    async def _list():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            url = f"{host_url}/api/shares"
            if project:
                url += f"?project_name={project}"

            console.print("[blue]Fetching share links...[/blue]")

            response = await make_api_request(
                method="GET",
                url=url,
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
) -> None:
    """Update a share link: enable/disable it or change its expiration.

    Examples:
      bm cloud share update abc123 --disable
      bm cloud share update abc123 --enable
      bm cloud share update abc123 --expires-at 2026-01-01
      bm cloud share update abc123 --expires-at none
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
) -> None:
    """Revoke (delete) a public share link.

    Examples:
      bm cloud share revoke abc123
      bm cloud share revoke abc123 --force
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
