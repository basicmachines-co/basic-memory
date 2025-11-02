"""Core cloud commands for Basic Memory CLI."""

import asyncio

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.auth import CLIAuth
from basic_memory.config import ConfigManager
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
    get_cloud_config,
    make_api_request,
)
from basic_memory.cli.commands.cloud.bisync_commands import (
    BisyncError,
    generate_mount_credentials,
    get_mount_info,
)
from basic_memory.cli.commands.cloud.rclone_config import configure_rclone_remote
from basic_memory.cli.commands.cloud.rclone_installer import (
    RcloneInstallError,
    install_rclone,
)

console = Console()


@cloud_app.command()
def login():
    """Authenticate with WorkOS using OAuth Device Authorization flow and enable cloud mode."""

    async def _login():
        client_id, domain, host_url = get_cloud_config()
        auth = CLIAuth(client_id=client_id, authkit_domain=domain)

        try:
            success = await auth.login()
            if not success:
                console.print("[red]Login failed[/red]")
                raise typer.Exit(1)

            # Test subscription access by calling a protected endpoint
            console.print("[dim]Verifying subscription access...[/dim]")
            await make_api_request("GET", f"{host_url.rstrip('/')}/proxy/health")

            # Enable cloud mode after successful login and subscription validation
            config_manager = ConfigManager()
            config = config_manager.load_config()
            config.cloud_mode = True
            config_manager.save_config(config)

            console.print("[green]Cloud mode enabled[/green]")
            console.print(f"[dim]All CLI commands now work against {host_url}[/dim]")

        except SubscriptionRequiredError as e:
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            console.print(
                "[dim]Once you have an active subscription, run [bold]bm cloud login[/bold] again.[/dim]"
            )
            raise typer.Exit(1)

    asyncio.run(_login())


@cloud_app.command()
def logout():
    """Disable cloud mode and return to local mode."""

    # Disable cloud mode
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_mode = False
    config_manager.save_config(config)

    console.print("[green]Cloud mode disabled[/green]")
    console.print("[dim]All CLI commands now work locally[/dim]")


@cloud_app.command("status")
def status() -> None:
    """Check cloud mode status and cloud instance health."""
    # Check cloud mode
    config_manager = ConfigManager()
    config = config_manager.load_config()

    console.print("[bold blue]Cloud Mode Status[/bold blue]")
    if config.cloud_mode:
        console.print("  Mode: [green]Cloud (enabled)[/green]")
        console.print(f"  Host: {config.cloud_host}")
        console.print("  [dim]All CLI commands work against cloud[/dim]")
    else:
        console.print("  Mode: [yellow]Local (disabled)[/yellow]")
        console.print("  [dim]All CLI commands work locally[/dim]")
        console.print("\n[dim]To enable cloud mode, run: bm cloud login[/dim]")
        return

    # Get cloud configuration
    _, _, host_url = get_cloud_config()
    host_url = host_url.rstrip("/")

    # Prepare headers
    headers = {}

    try:
        console.print("\n[blue]Checking cloud instance health...[/blue]")

        # Make API request to check health
        response = asyncio.run(
            make_api_request(method="GET", url=f"{host_url}/proxy/health", headers=headers)
        )

        health_data = response.json()

        console.print("[green]Cloud instance is healthy[/green]")

        # Display status details
        if "status" in health_data:
            console.print(f"  Status: {health_data['status']}")
        if "version" in health_data:
            console.print(f"  Version: {health_data['version']}")
        if "timestamp" in health_data:
            console.print(f"  Timestamp: {health_data['timestamp']}")

        console.print("\n[dim]To sync projects, use: bm project bisync --name <project>[/dim]")

    except CloudAPIError as e:
        console.print(f"[red]Error checking cloud health: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("setup")
def setup() -> None:
    """Set up cloud sync by installing rclone and configuring credentials.

    SPEC-20: Simplified to project-scoped workflow.
    After setup, use project commands for syncing:
      bm project add <name> <path> --local-path ~/projects/<name>
      bm project bisync --name <name> --resync  # First time
      bm project bisync --name <name>            # Subsequent syncs
    """
    console.print("[bold blue]Basic Memory Cloud Setup[/bold blue]")
    console.print("Setting up cloud sync with rclone...\n")

    try:
        # Step 1: Install rclone
        console.print("[blue]Step 1: Installing rclone...[/blue]")
        install_rclone()

        # Step 2: Get tenant info
        console.print("\n[blue]Step 2: Getting tenant information...[/blue]")
        tenant_info = asyncio.run(get_mount_info())
        console.print(f"[green]Found tenant: {tenant_info.tenant_id}[/green]")

        # Step 3: Generate credentials
        console.print("\n[blue]Step 3: Generating sync credentials...[/blue]")
        creds = asyncio.run(generate_mount_credentials(tenant_info.tenant_id))
        console.print("[green]Generated secure credentials[/green]")

        # Step 4: Configure rclone remote
        console.print("\n[blue]Step 4: Configuring rclone remote...[/blue]")
        configure_rclone_remote(
            access_key=creds.access_key,
            secret_key=creds.secret_key,
        )

        console.print("\n[bold green]Cloud setup completed successfully![/bold green]")
        console.print("\n[bold]Next steps:[/bold]")
        console.print("1. Add a project with local sync path:")
        console.print("   bm project add research --local-path ~/Documents/research")
        console.print("\n   Or configure sync for an existing project:")
        console.print("   bm project sync-setup research ~/Documents/research")
        console.print("\n2. Preview the initial sync (recommended):")
        console.print("   bm project bisync --name research --resync --dry-run")
        console.print("\n3. If all looks good, run the actual sync:")
        console.print("   bm project bisync --name research --resync")
        console.print("\n4. Subsequent syncs (no --resync needed):")
        console.print("   bm project bisync --name research")
        console.print(
            "\n[dim]Tip: Always use --dry-run first to preview changes before syncing[/dim]"
        )

    except (RcloneInstallError, BisyncError, CloudAPIError) as e:
        console.print(f"\n[red]Setup failed: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Unexpected error during setup: {e}[/red]")
        raise typer.Exit(1)
