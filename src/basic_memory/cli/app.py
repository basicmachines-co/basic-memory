# This prevents DEBUG logs from appearing on stdout during module-level
# initialization (e.g., template_loader.TemplateLoader() logs at DEBUG level).
from loguru import logger

logger.remove()

from typing import Optional  # noqa: E402

import typer  # noqa: E402

from basic_memory.cli.auto_update import maybe_run_periodic_auto_update  # noqa: E402
from basic_memory.cli.container import CliContainer, set_container  # noqa: E402
from basic_memory.cli.promo import maybe_show_cloud_promo, maybe_show_init_line  # noqa: E402
from basic_memory.config import init_cli_logging  # noqa: E402
import logfire  # noqa: E402


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:  # pragma: no cover
        import basic_memory

        typer.echo(f"Basic Memory version: {basic_memory.__version__}")
        raise typer.Exit()


app = typer.Typer(name="basic-memory")


@app.callback()
def app_callback(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Basic Memory - Local-first personal knowledge management."""

    command_name = ctx.invoked_subcommand or "root"

    # Trigger: a `hook` invocation (the advisory harness front door, SPEC-55).
    # Why: the hook verbs need none of the global composition root — they resolve
    # config lazily via ConfigManager when they run, the lifecycle verbs
    # (session-start/pre-compact) each wrap their body in _run_fail_open, and the
    # operator verbs (install/remove) touch no global config at all. Everything
    # the callback does here — logging setup (Logfire loads config), the span,
    # the container, uvloop — can raise SystemExit on a malformed config
    # (ConfigManager reports bad JSON that way), and none of it may abort the verb
    # before its own guard: even config-free work (envelope capture, `hook
    # install`) must still run.
    # Outcome: run that setup best-effort, swallowing (Exception, SystemExit) so a
    # broken config surfaces only where it belongs — a lifecycle verb's fail-open
    # guard, or an operator verb that needs config. Skip global init and the
    # promo/init-line/auto-update messaging (off the session-start/pre-compact hot
    # path, out of the brief). KeyboardInterrupt is left to propagate.
    if ctx.invoked_subcommand == "hook":
        try:
            init_cli_logging()
            ctx.with_resource(
                logfire.span(
                    f"cli.command.{command_name}",
                    entrypoint="cli",
                    command_name=command_name,
                )
            )
            container = CliContainer.create()
            set_container(container)
            # uvloop must own the event-loop policy before the hook verbs run
            # async search/write/flush through run_with_cleanup's asyncio.run(),
            # or a Postgres backend hits the asyncpg engine-dispose race
            # (#831/#877). No-op for SQLite, so hook startup stays light.
            from basic_memory.db import maybe_install_uvloop

            maybe_install_uvloop(container.config)
        except (Exception, SystemExit):
            pass
        return

    # Initialize logging for CLI (file only, no stdout)
    init_cli_logging()
    ctx.with_resource(
        logfire.span(
            f"cli.command.{command_name}",
            entrypoint="cli",
            command_name=command_name,
        )
    )

    # --- Composition Root ---
    # Create container and read config (single point of config access)
    container = CliContainer.create()
    set_container(container)

    # Trigger: Postgres backend resolved at CLI startup, before any asyncio.run().
    # Why: uvloop must own the event-loop policy before the loop is created so the
    # asyncpg engine-dispose race (#831/#877) cannot fire. No-op for SQLite.
    # Outcome: subsequent asyncio.run() calls in CLI commands use uvloop on Postgres.
    from basic_memory.db import maybe_install_uvloop

    maybe_install_uvloop(container.config)

    # Trigger: first-run init confirmation before command output.
    # Why: informational "initialized" message belongs above command results, not in the upsell panel.
    # Outcome: one-time plain line printed before the subcommand runs.
    maybe_show_init_line(ctx.invoked_subcommand)

    # Trigger: register post-command messaging callbacks.
    # Why: informational/promo/update output belongs below command results.
    # Outcome: command output remains primary, with optional follow-up notices afterwards.
    def _post_command_messages() -> None:
        maybe_show_cloud_promo(ctx.invoked_subcommand)
        maybe_run_periodic_auto_update(ctx.invoked_subcommand)

    ctx.call_on_close(_post_command_messages)

    # Run initialization for commands that don't use the API
    # Skip for 'mcp' command - it has its own lifespan that handles initialization
    # Skip for API-using commands (status, sync, etc.) - they handle initialization via deps.py
    # Skip for 'reset' command - it manages its own database lifecycle
    # Skip for 'man' - it only copies packaged files; a broken local database
    # must not block installing the offline docs
    # ('hook' returns above, before this point.)
    skip_init_commands = {
        "doctor",
        "man",
        "mcp",
        "status",
        "sync",
        "project",
        "config",
        "tool",
        "reset",
        "reindex",
        "update",
        "watch",
        "workspace",
    }
    if (
        not version
        and ctx.invoked_subcommand is not None
        and ctx.invoked_subcommand not in skip_init_commands
    ):
        from basic_memory.services.initialization import ensure_initialization

        ensure_initialization(container.config)


## import
# Register sub-command groups
import_app = typer.Typer(help="Import data from various sources")
app.add_typer(import_app, name="import")

claude_app = typer.Typer(help="Import Conversations from Claude JSON export.")
import_app.add_typer(claude_app, name="claude")


## cloud

cloud_app = typer.Typer(help="Access Basic Memory Cloud")
app.add_typer(cloud_app, name="cloud")
