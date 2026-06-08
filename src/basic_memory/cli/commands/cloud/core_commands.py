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
from basic_memory.cli.commands.cloud.rclone_config import (
    configure_rclone_remote,
    rclone_remote_exists,
    remote_name_for_workspace,
)
from basic_memory.cli.commands.cloud.rclone_installer import (
    RcloneInstallError,
    install_rclone,
)
from basic_memory.mcp.project_context import get_available_workspaces
from basic_memory.schemas.cloud import (
    WorkspaceInfo,
    format_workspace_choices,
    format_workspace_selection_choices,
    workspace_matches_exact_identifier,
)

console = Console()


def _resolve_setup_workspace(identifier: str) -> WorkspaceInfo:
    """Resolve a workspace identifier (slug, name, or tenant_id) for setup.

    Errors with copyable choices when the identifier matches zero or multiple
    workspaces, so the user can disambiguate.
    """
    workspaces = run_with_cleanup(get_available_workspaces())
    if not workspaces:
        console.print("[red]No accessible cloud workspaces found for this account[/red]")
        raise typer.Exit(1)

    matches = [ws for ws in workspaces if workspace_matches_exact_identifier(ws, identifier)]
    if len(matches) == 1:
        return matches[0]

    if not matches:
        console.print(f"[red]No workspace matches '{identifier}'[/red]")
        console.print("\nAvailable workspaces:")
        console.print(format_workspace_choices(workspaces))
    else:
        console.print(f"[red]'{identifier}' matches multiple workspaces[/red]")
        console.print("\nDisambiguate with the workspace slug or tenant_id:")
        console.print(format_workspace_selection_choices(matches))
    raise typer.Exit(1)


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

        # Trigger: the subscription-check call (/proxy/health) returned any error
        #   that is NOT a recognized subscription_required 403 — e.g. a 5xx while the
        #   tenant instance is still provisioning, a 403/401 whose body doesn't match
        #   the subscription_required shape, or a connection failure.
        # Why: OAuth already succeeded and tokens are saved at this point, so a raw
        #   traceback (the old behavior) misleads users into thinking login itself
        #   failed. See #863.
        # Outcome: surface a clean, actionable message and exit non-zero instead of
        #   crashing. make_api_request wraps every httpx error (status + transport)
        #   in CloudAPIError, so this single handler covers them all.
        except CloudAPIError as e:
            console.print("\n[yellow]Authenticated, but couldn't verify cloud access.[/yellow]\n")
            console.print(f"[dim]{e}[/dim]\n")
            console.print(
                "Your workspace may still be provisioning. Wait a moment, then check with "
                "[bold]bm cloud status[/bold] or retry [bold]bm cloud login[/bold].\n"
            )
            console.print(
                "[dim]If this persists, contact support at "
                "[blue underline]https://basicmemory.com[/blue underline].[/dim]"
            )
            raise typer.Exit(1)

    run_with_cleanup(_login())


@cloud_app.command()
def logout():
    """Remove stored OAuth tokens and clear cached workspace selection."""
    config_manager = ConfigManager()
    config = config_manager.config
    auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
    auth.logout()

    # Trigger: ending a session must invalidate the cached workspace.
    # Why: a follow-up `bm cloud login` (often as a different user, or returning
    #      from an org workspace to personal) inherits the previous selection
    #      and silently routes everything through the wrong tenant. See #755.
    # Outcome: re-login starts from a clean slate; the user picks again via
    #      `bm cloud workspace set-default` or per-project --workspace.
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
def setup(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        help="Set up sync for a specific workspace (slug, name, or tenant_id). "
        "Omit for your default workspace.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Reconfigure an rclone remote that already exists (mints new credentials).",
    ),
) -> None:
    """Set up cloud sync by installing rclone and configuring credentials.

    Run once per workspace you sync. The default workspace uses the
    'basic-memory-cloud' remote; other (e.g. Team) workspaces each get their own
    tenant-scoped remote, since Tigris credentials are bucket-scoped.

    After setup, use the cloud sync commands:
      bm cloud pull --name <name>   # fetch cloud changes (Team-safe)
      bm cloud push --name <name>   # upload local changes (Team-safe)
    """
    console.print("[bold blue]Basic Memory Cloud Setup[/bold blue]")
    console.print("Setting up cloud sync with rclone...\n")

    try:
        # Step 1: Install rclone
        console.print("[blue]Step 1: Installing rclone...[/blue]")
        install_rclone()

        # --- Resolve target workspace ---
        # Trigger: --workspace given. Why: Tigris keys are tenant-scoped, so a
        # non-default workspace needs its own bucket + remote. Outcome: scope the
        # mount-info/credentials calls and name the remote after the workspace.
        if workspace is not None:
            target = _resolve_setup_workspace(workspace)
            workspace_id: str | None = target.tenant_id
            remote_name = remote_name_for_workspace(target.slug, is_default=target.is_default)
            console.print(f"[dim]Workspace: {target.name} ({target.slug})[/dim]")
        else:
            workspace_id = None  # default tenant
            remote_name = remote_name_for_workspace(None, is_default=True)

        # Trigger: the target rclone remote already exists.
        # Why: re-running setup mints new credentials and overwrites the remote,
        # silently repointing it (e.g. clobbering the shared basic-memory-cloud
        # that served another tenant) — the #922 footgun. Checked BEFORE minting
        # so an abort wastes no credentials.
        # Outcome: stop unless the user explicitly opts in with --force.
        if rclone_remote_exists(remote_name) and not force:
            console.print(f"[red]rclone remote '{remote_name}' is already configured.[/red]")
            console.print(
                "Re-running setup mints new credentials and overwrites it. "
                "Pass --force to reconfigure."
            )
            raise typer.Exit(1)

        # Step 2: Get tenant info (scoped to the target workspace when given)
        console.print("\n[blue]Step 2: Getting tenant information...[/blue]")
        tenant_info = run_with_cleanup(get_mount_info(workspace_id=workspace_id))
        console.print(f"[green]Found tenant: {tenant_info.tenant_id}[/green]")

        # Step 3: Generate credentials for that tenant's bucket
        console.print("\n[blue]Step 3: Generating sync credentials...[/blue]")
        creds = run_with_cleanup(generate_mount_credentials(tenant_info.tenant_id))
        console.print("[green]Generated secure credentials[/green]")

        # Step 4: Configure the tenant's rclone remote
        console.print("\n[blue]Step 4: Configuring rclone remote...[/blue]")
        configure_rclone_remote(
            access_key=creds.access_key,
            secret_key=creds.secret_key,
            remote_name=remote_name,
        )

        console.print("\n[bold green]Cloud setup completed successfully![/bold green]")
        console.print("\n[bold]Next steps:[/bold]")
        console.print("1. Configure sync for a project:")
        console.print("   bm cloud sync-setup research ~/Documents/research")
        console.print("\n2. Preview a pull (recommended):")
        console.print("   bm cloud pull --name research --dry-run")
        console.print("\n3. Fetch cloud changes / upload local changes:")
        console.print("   bm cloud pull --name research")
        console.print("   bm cloud push --name research")
        console.print(
            "\n[dim]Tip: Always use --dry-run first to preview changes before syncing[/dim]"
        )

    except (RcloneInstallError, BisyncError, CloudAPIError) as e:
        console.print(f"\n[red]Setup failed: {e}[/red]")
        raise typer.Exit(1)
    except typer.Exit:
        raise
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
