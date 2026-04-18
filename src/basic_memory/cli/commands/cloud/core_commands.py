"""Core cloud commands for Basic Memory CLI."""

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.auth import CLIAuth
from basic_memory.cli.analytics import (
    track,
    EVENT_CLOUD_LOGIN_STARTED,
    EVENT_CLOUD_LOGIN_SUCCESS,
    EVENT_CLOUD_LOGIN_SUB_REQUIRED,
    EVENT_PROMO_OPTED_OUT,
)
from basic_memory.cli.promo import OSS_DISCOUNT_CODE
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
from basic_memory.mcp.project_context import get_available_workspaces

console = Console()


async def _select_default_workspace_on_login() -> None:
    """Prompt workspace selection after login when multiple workspaces exist.

    Single workspace: auto-set as default silently.
    Multiple workspaces: show a numbered list and prompt for selection.
    Failure is non-fatal — user can always run 'bm cloud workspace set-default'.
    """
    try:
        workspaces = await get_available_workspaces()
    except Exception:
        console.print(
            "[dim]Workspace discovery unavailable; run 'bm cloud workspace set-default' if needed.[/dim]"
        )
        return

    if not workspaces:
        return

    config_manager = ConfigManager()
    config = config_manager.config

    if len(workspaces) == 1:
        config.default_workspace = workspaces[0].tenant_id
        config_manager.save_config(config)
        console.print(f"[dim]Default workspace: {workspaces[0].name}[/dim]")
        return

    # Multiple workspaces — prompt user to pick one.
    console.print("\n[bold]Multiple workspaces available:[/bold]")
    for i, ws in enumerate(workspaces, 1):
        console.print(f"  {i}. {ws.name} ({ws.workspace_type}) — {ws.tenant_id}")

    raw = typer.prompt(
        "Select default workspace (number, or press Enter to skip)",
        default="",
    )
    raw = raw.strip()

    if not raw:
        console.print(
            "[dim]No default workspace set; run 'bm cloud workspace set-default' to choose.[/dim]"
        )
        return

    try:
        idx = int(raw) - 1
    except ValueError:
        console.print(
            f"[yellow]'{raw}' is not a valid number; run 'bm cloud workspace set-default' to choose.[/yellow]"
        )
        return

    if 0 <= idx < len(workspaces):
        selected = workspaces[idx]
        config.default_workspace = selected.tenant_id
        config_manager.save_config(config)
        console.print(f"[green]Default workspace set to '{selected.name}'[/green]")
    else:
        console.print(
            f"[yellow]Selection out of range; run 'bm cloud workspace set-default' to choose.[/yellow]"
        )


@cloud_app.command()
def login():
    """Authenticate with WorkOS using OAuth Device Authorization flow."""

    async def _login():
        track(EVENT_CLOUD_LOGIN_STARTED)
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

            track(EVENT_CLOUD_LOGIN_SUCCESS)
            console.print("[green]Cloud authentication successful[/green]")
            console.print(f"[dim]Cloud host ready: {host_url}[/dim]")

            # Prompt workspace selection when multiple are available so users
            # don't get silently locked to a stale default_workspace from a
            # previous session.
            await _select_default_workspace_on_login()

        except SubscriptionRequiredError as e:
            track(EVENT_CLOUD_LOGIN_SUB_REQUIRED)
            console.print("\n[red]Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(
                f"OSS discount code: [bold]{OSS_DISCOUNT_CODE}[/bold] (20% off for 3 months)\n"
            )
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            console.print(
                "[dim]Once you have an active subscription, run [bold]bm cloud login[/bold] again.[/dim]"
            )
            raise typer.Exit(1)

    run_with_cleanup(_login())


@cloud_app.command()
def logout():
    """Remove stored OAuth tokens and reset workspace selection."""
    config_manager = ConfigManager()
    config = config_manager.config
    auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
    auth.logout()

    # Trigger: session is ending, so any previously selected workspace is no
    # longer meaningful for the next authenticated user.
    # Why: prevents stale default_workspace from silently routing to the wrong
    #   tenant (e.g., an org workspace) on re-login.
    # Outcome: next login will prompt workspace selection afresh.
    if config.default_workspace is not None:
        config.default_workspace = None
        config_manager.save_config(config)

    console.print("[dim]API key (if configured) remains available for cloud project routing.[/dim]")


@cloud_app.command("status")
def status() -> None:
    """Check cloud authentication and connection status."""
    config_manager = ConfigManager()
    config = config_manager.load_config()
    auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
    tokens = auth.load_tokens()

    console.print("[bold blue]Cloud Status[/bold blue]")
    console.print(f"  Host: {config.cloud_host}")
    console.print(
        f"  API Key: {'[green]configured[/green]' if config.cloud_api_key else '[yellow]not set[/yellow]'}"
    )

    oauth_status = "[yellow]not logged in[/yellow]"
    if tokens:
        if auth.is_token_valid(tokens):
            oauth_status = "[green]token valid[/green]"
        else:
            oauth_status = "[yellow]token expired[/yellow]"
    console.print(f"  OAuth: {oauth_status}")

    has_credentials = bool(config.cloud_api_key) or tokens is not None
    if not has_credentials:
        console.print(
            "\n[dim]No cloud credentials found. Run: bm cloud login or bm cloud api-key save <key>[/dim]"
        )
        return

    # Quick connection check — just verify we can reach the cloud
    _, _, host_url = get_cloud_config()
    host_url = host_url.rstrip("/")

    try:
        run_with_cleanup(make_api_request(method="GET", url=f"{host_url}/proxy/health"))
        console.print("\n[green]Cloud connected[/green]")
    except CloudAPIError:
        console.print("\n[yellow]Cloud not connected[/yellow]")
        console.print(
            "[dim]Try re-authenticating with 'bm cloud login' or 'bm cloud api-key save'.[/dim]"
        )
    except Exception:
        console.print("\n[yellow]Cloud not connected[/yellow]")


@cloud_app.command("setup")
def setup() -> None:
    """Set up cloud sync by installing rclone and configuring credentials.

    After setup, use project commands for syncing:
      bm project add <name> --cloud --local-path ~/projects/<name>
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
        tenant_info = run_with_cleanup(get_mount_info())
        console.print(f"[green]Found tenant: {tenant_info.tenant_id}[/green]")

        # Step 3: Generate credentials
        console.print("\n[blue]Step 3: Generating sync credentials...[/blue]")
        creds = run_with_cleanup(generate_mount_credentials(tenant_info.tenant_id))
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
        console.print("   bm project add research --cloud --local-path ~/Documents/research")
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


@cloud_app.command("promo")
def promo(enabled: bool = typer.Option(True, "--on/--off", help="Enable or disable CLI promos.")):
    """Enable or disable CLI cloud promo messages."""
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_promo_opt_out = not enabled
    config_manager.save_config(config)

    if enabled:
        console.print("[green]Cloud promo messages enabled[/green]")
    else:
        track(EVENT_PROMO_OPTED_OUT)
        console.print("[yellow]Cloud promo messages disabled[/yellow]")


# --- API key management subcommand group ---

api_key_app = typer.Typer(help="Manage cloud API keys")
cloud_app.add_typer(api_key_app, name="api-key")


@api_key_app.command("save")
def api_key_save(
    api_key: str = typer.Argument(..., help="API key (bmc_ prefixed) for cloud access"),
) -> None:
    """Save an existing API key to local config.

    Use when you already have an API key (e.g., from the web app).

    Example:
      bm cloud api-key save bmc_abc123...
    """
    if not api_key.startswith("bmc_"):
        console.print("[red]Error: API key must start with 'bmc_'[/red]")
        raise typer.Exit(1)

    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_api_key = api_key
    config_manager.save_config(config)

    console.print("[green]API key saved[/green]")
    console.print("[dim]Projects set to cloud mode will use this key for authentication[/dim]")
    console.print("[dim]Set a project to cloud mode: bm project set-cloud <name>[/dim]")


@api_key_app.command("create")
def api_key_create(
    name: str = typer.Argument(..., help="Human-readable name for the API key"),
) -> None:
    """Create a new API key via the cloud API and save it locally.

    Requires active OAuth session (run 'bm cloud login' first).

    Example:
      bm cloud api-key create "my-laptop"
    """

    async def _create_key():
        _, _, host_url = get_cloud_config()
        host_url = host_url.rstrip("/")

        console.print(f"[dim]Creating API key '{name}'...[/dim]")
        response = await make_api_request(
            method="POST",
            url=f"{host_url}/api/keys",
            json_data={"name": name},
        )

        key_data = response.json()
        api_key = key_data.get("key")
        if not api_key:
            console.print("[red]Error: No key returned from API[/red]")
            raise typer.Exit(1)

        # Save to config
        config_manager = ConfigManager()
        config = config_manager.load_config()
        config.cloud_api_key = api_key
        config_manager.save_config(config)

        console.print(f"[green]API key '{name}' created and saved[/green]")
        console.print("[dim]Projects set to cloud mode will use this key for authentication[/dim]")
        console.print("[dim]Set a project to cloud mode: bm project set-cloud <name>[/dim]")

    try:
        run_with_cleanup(_create_key())
    except CloudAPIError as e:
        console.print(f"[red]Error creating API key: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)
