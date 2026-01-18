"""Restore CLI commands for Basic Memory Cloud.

SPEC-29 Phase 3: CLI commands for restoring files from Tigris bucket snapshots.
"""

import asyncio

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
    make_api_request,
)
from basic_memory.cli.commands.cloud.schemas import BucketSnapshotBrowseResponse
from basic_memory.config import ConfigManager

console = Console()


@cloud_app.command("restore")
def restore(
    path: str = typer.Argument(
        ...,
        help="Path to restore (file or folder, e.g., 'notes/project.md' or 'research/')",
    ),
    snapshot_id: str = typer.Option(
        ...,
        "--snapshot",
        "-s",
        help="ID of the snapshot to restore from",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Restore a file or folder from a snapshot.

    This command restores files from a previous snapshot to the current bucket.
    The restored files will overwrite any existing files at the same path.

    Examples:
      bm cloud restore notes/project.md --snapshot abc123
      bm cloud restore research/ --snapshot abc123
      bm cloud restore notes/project.md --snapshot abc123 --force
    """

    async def _restore():
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            host_url = config.cloud_host.rstrip("/")

            # Normalize path - remove leading slash if present
            normalized_path = path.lstrip("/")

            if not force:
                # Show what will be restored
                console.print(f"[blue]Preparing to restore from snapshot {snapshot_id}[/blue]")
                console.print(f"  Path: {normalized_path}")

                # Try to browse the snapshot to show what files will be affected
                try:
                    browse_url = f"{host_url}/api/bucket-snapshots/{snapshot_id}/browse"
                    if normalized_path:
                        browse_url += f"?prefix={normalized_path}"

                    response = await make_api_request(
                        method="GET",
                        url=browse_url,
                    )
                    browse_response = BucketSnapshotBrowseResponse.model_validate(response.json())

                    if browse_response.files:
                        if len(browse_response.files) <= 10:
                            console.print("\n  Files to restore:")
                            for file_info in browse_response.files:
                                console.print(f"    - {file_info.key}")
                        else:
                            console.print(
                                f"\n  {len(browse_response.files)} files will be restored"
                            )
                            console.print("  First 5 files:")
                            for file_info in browse_response.files[:5]:
                                console.print(f"    - {file_info.key}")
                            console.print(f"    ... and {len(browse_response.files) - 5} more")
                    else:
                        console.print(
                            f"\n[yellow]No files found matching '{normalized_path}' "
                            f"in snapshot[/yellow]"
                        )
                        raise typer.Exit(0)

                except CloudAPIError as browse_error:
                    if browse_error.status_code == 404:
                        console.print(f"[red]Snapshot not found: {snapshot_id}[/red]")
                        raise typer.Exit(1)
                    # If browse fails for other reasons, proceed with confirmation anyway
                    pass

                console.print(
                    "\n[yellow]Warning: Restored files will overwrite existing files![/yellow]"
                )
                confirmed = typer.confirm("\nProceed with restore?")
                if not confirmed:
                    console.print("[yellow]Restore cancelled[/yellow]")
                    raise typer.Exit(0)

            console.print(f"[blue]Restoring from snapshot {snapshot_id}...[/blue]")

            response = await make_api_request(
                method="POST",
                url=f"{host_url}/api/bucket-snapshots/{snapshot_id}/restore",
                json_data={"path": normalized_path},
            )

            data = response.json()
            restored_files = data.get("restored", [])
            returned_snapshot_id = data.get("snapshot_id", snapshot_id)

            if restored_files:
                console.print(f"[green]Successfully restored {len(restored_files)} file(s)[/green]")
                if len(restored_files) <= 10:
                    for file_path in restored_files:
                        console.print(f"  - {file_path}")
                else:
                    console.print("  First 5 restored files:")
                    for file_path in restored_files[:5]:
                        console.print(f"  - {file_path}")
                    console.print(f"  ... and {len(restored_files) - 5} more")
                console.print(f"\n[dim]Snapshot ID: {returned_snapshot_id}[/dim]")
            else:
                console.print("[yellow]No files were restored[/yellow]")
                console.print(f"[dim]No files matching '{normalized_path}' found in snapshot[/dim]")

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
                console.print(f"[red]Failed to restore: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_restore())
