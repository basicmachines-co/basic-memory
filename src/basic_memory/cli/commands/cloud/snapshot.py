"""Snapshot CLI commands for Basic Memory Cloud.

SPEC-29 Phase 3: CLI commands for managing Tigris bucket snapshots.
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
from basic_memory.cli.commands.cloud.schemas import BucketSnapshotBrowseResponse
from basic_memory.config import ConfigManager

console = Console()
snapshot_app = typer.Typer(help="Manage bucket snapshots")


def _format_timestamp(iso_timestamp: str) -> str:
    """Format ISO timestamp to a human-readable format."""
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return iso_timestamp


@snapshot_app.command("create")
def create(
    description: str = typer.Argument(
        ...,
        help="Description for the snapshot",
    ),
) -> None:
    """Create a new bucket snapshot.

    Examples:
      bm cloud snapshot create "before major refactor"
      bm cloud snapshot create "daily backup"
    """

    async def _create():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            console.print("[blue]Creating snapshot...[/blue]")

            response = await make_api_request(
                method="POST",
                url=f"{host_url}/api/bucket-snapshots",
                json_data={"description": description},
            )

            data = response.json()
            snapshot_id = data.get("id", "unknown")
            snapshot_version = data.get("snapshot_version", "unknown")
            created_at = _format_timestamp(data.get("created_at", ""))

            console.print("[green]Snapshot created successfully[/green]")
            console.print(f"  ID: {snapshot_id}")
            console.print(f"  Version: {snapshot_version}")
            console.print(f"  Created: {created_at}")
            console.print(f"  Description: {description}")

        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            console.print(f"[red]Failed to create snapshot: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_create())


@snapshot_app.command("list")
def list_snapshots(
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of snapshots to display",
    ),
) -> None:
    """List all bucket snapshots.

    Examples:
      bm cloud snapshot list
      bm cloud snapshot list --limit 20
    """

    async def _list():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            console.print("[blue]Fetching snapshots...[/blue]")

            response = await make_api_request(
                method="GET",
                url=f"{host_url}/api/bucket-snapshots",
            )

            data = response.json()
            snapshots = data.get("snapshots", [])
            total = data.get("total", len(snapshots))

            if not snapshots:
                console.print("[yellow]No snapshots found[/yellow]")
                console.print(
                    '\n[dim]Create a snapshot with: bm cloud snapshot create "description"[/dim]'
                )
                return

            # Create a table for displaying snapshots
            table = Table(title=f"Bucket Snapshots ({total} total)")
            table.add_column("ID", style="cyan", no_wrap=True)
            table.add_column("Description", style="white")
            table.add_column("Auto", style="dim")
            table.add_column("Created", style="green")

            for snapshot in snapshots[:limit]:
                snapshot_id = snapshot.get("id", "unknown")
                desc = snapshot.get("description") or snapshot.get("name", "-")
                auto = "yes" if snapshot.get("auto", False) else "no"
                created_at = _format_timestamp(snapshot.get("created_at", ""))

                table.add_row(snapshot_id, desc, auto, created_at)

            console.print(table)

            if total > limit:
                console.print(
                    f"\n[dim]Showing {limit} of {total} snapshots. Use --limit to see more.[/dim]"
                )

        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            console.print(f"[red]Failed to list snapshots: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_list())


@snapshot_app.command("delete")
def delete(
    snapshot_id: str = typer.Argument(
        ...,
        help="The ID of the snapshot to delete",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Delete a bucket snapshot.

    Examples:
      bm cloud snapshot delete abc123
      bm cloud snapshot delete abc123 --force
    """

    async def _delete():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            if not force:
                # Fetch snapshot details first to show what will be deleted
                console.print("[blue]Fetching snapshot details...[/blue]")
                try:
                    response = await make_api_request(
                        method="GET",
                        url=f"{host_url}/api/bucket-snapshots/{snapshot_id}",
                    )
                    data = response.json()
                    desc = data.get("description") or data.get("name", "unnamed")
                    created_at = _format_timestamp(data.get("created_at", ""))
                    console.print("\nSnapshot to delete:")
                    console.print(f"  ID: {snapshot_id}")
                    console.print(f"  Description: {desc}")
                    console.print(f"  Created: {created_at}")
                except CloudAPIError:
                    # If we can't fetch details, proceed with confirmation anyway
                    pass

                confirmed = typer.confirm("\nAre you sure you want to delete this snapshot?")
                if not confirmed:
                    console.print("[yellow]Deletion cancelled[/yellow]")
                    raise typer.Exit(0)

            console.print("[blue]Deleting snapshot...[/blue]")

            await make_api_request(
                method="DELETE",
                url=f"{host_url}/api/bucket-snapshots/{snapshot_id}",
            )

            console.print(f"[green]Snapshot {snapshot_id} deleted successfully[/green]")

        except typer.Exit:
            # Re-raise typer.Exit without modification - it's used for clean exits
            raise
        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            if e.status_code == 404:
                console.print(f"[red]Snapshot not found: {snapshot_id}[/red]")
            else:
                console.print(f"[red]Failed to delete snapshot: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_delete())


@snapshot_app.command("show")
def show(
    snapshot_id: str = typer.Argument(
        ...,
        help="The ID of the snapshot to show",
    ),
) -> None:
    """Show details of a specific snapshot.

    Examples:
      bm cloud snapshot show abc123
    """

    async def _show():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            response = await make_api_request(
                method="GET",
                url=f"{host_url}/api/bucket-snapshots/{snapshot_id}",
            )

            data = response.json()

            console.print("[bold blue]Snapshot Details[/bold blue]")
            console.print(f"  ID: {data.get('id', 'unknown')}")
            console.print(f"  Bucket: {data.get('bucket_name', 'unknown')}")
            console.print(f"  Version: {data.get('snapshot_version', 'unknown')}")
            console.print(f"  Name: {data.get('name', '-')}")
            console.print(f"  Description: {data.get('description') or '-'}")
            console.print(f"  Auto: {'yes' if data.get('auto', False) else 'no'}")
            console.print(f"  Created: {_format_timestamp(data.get('created_at', ''))}")

        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            if e.status_code == 404:
                console.print(f"[red]Snapshot not found: {snapshot_id}[/red]")
            else:
                console.print(f"[red]Failed to get snapshot details: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_show())


@snapshot_app.command("browse")
def browse(
    snapshot_id: str = typer.Argument(
        ...,
        help="The ID of the snapshot to browse",
    ),
    prefix: Optional[str] = typer.Option(
        None,
        "--prefix",
        "-p",
        help="Filter files by path prefix (e.g., 'notes/')",
    ),
) -> None:
    """Browse contents of a snapshot.

    Examples:
      bm cloud snapshot browse abc123
      bm cloud snapshot browse abc123 --prefix notes/
    """

    async def _browse():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            url = f"{host_url}/api/bucket-snapshots/{snapshot_id}/browse"
            if prefix:
                url += f"?prefix={prefix}"

            response = await make_api_request(
                method="GET",
                url=url,
            )

            browse_response = BucketSnapshotBrowseResponse.model_validate(response.json())

            if not browse_response.files:
                if prefix:
                    console.print(f"[yellow]No files found with prefix '{prefix}'[/yellow]")
                else:
                    console.print("[yellow]No files found in snapshot[/yellow]")
                return

            console.print(
                f"[bold blue]Snapshot Contents ({len(browse_response.files)} files)[/bold blue]"
            )
            for file_info in browse_response.files:
                size_kb = file_info.size // 1024
                console.print(f"  {file_info.key} ({size_kb} KB)")

            console.print(
                f"\n[dim]Use 'bm cloud restore <path> --snapshot {snapshot_id}' "
                f"to restore files[/dim]"
            )

        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            raise typer.Exit(1)
        except CloudAPIError as e:
            if e.status_code == 404:
                console.print(f"[red]Snapshot not found: {snapshot_id}[/red]")
            else:
                console.print(f"[red]Failed to browse snapshot: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_browse())
