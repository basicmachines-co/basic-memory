"""Cloud promo messaging for CLI entrypoint."""

import os
import sys

from rich.console import Console
from rich.panel import Panel

import basic_memory
from basic_memory.config import ConfigManager

OSS_DISCOUNT_CODE = "BMFOSS"
CLOUD_LEARN_MORE_URL = "https://basicmemory.com"


def _promos_disabled_by_env() -> bool:
    """Check environment-level kill switch for promo output."""
    value = os.getenv("BASIC_MEMORY_NO_PROMOS", "").strip().lower()
    return value in {"1", "true", "yes"}


def _is_interactive_session() -> bool:
    """Return whether stdin/stdout are interactive terminals."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _build_cloud_promo_message() -> str:
    """Build benefit-led cloud upsell copy with Rich markup."""
    return (
        "☁️ [bold]Your knowledge, everywhere.[/bold] ✨\n"
        "Stop losing context when you switch machines.\n"
        "Basic Memory Cloud syncs your memory across every device, including mobile and web.\n"
        "Try it free for 7 days.\n"
        f"Use [bold cyan]{OSS_DISCOUNT_CODE}[/bold cyan] for 20% off when you subscribe.\n"
        "[bold green]→ bm cloud login[/bold green]"
    )


def maybe_show_init_line(
    invoked_subcommand: str | None,
    *,
    config_manager: ConfigManager | None = None,
    is_interactive: bool | None = None,
    console: Console | None = None,
) -> None:
    """Show a one-time init confirmation line before command output."""
    manager = config_manager or ConfigManager()
    config = manager.load_config()

    interactive = _is_interactive_session() if is_interactive is None else is_interactive

    # Same gates as the cloud promo — suppress in non-interactive, env kill-switch,
    # mcp/root-help contexts, or when already shown.
    if _promos_disabled_by_env() or not interactive:
        return

    if invoked_subcommand in {None, "mcp"}:
        return

    if config.cloud_promo_first_run_shown:
        return

    out = console or Console()
    out.print("Basic Memory initialized ✓")


def maybe_show_cloud_promo(
    invoked_subcommand: str | None,
    *,
    config_manager: ConfigManager | None = None,
    is_interactive: bool | None = None,
    console: Console | None = None,
) -> None:
    """Show cloud promo copy when discovery gates are satisfied."""
    manager = config_manager or ConfigManager()
    config = manager.load_config()
    from basic_memory.cli.auth import CLIAuth

    auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
    has_cloud_access = bool(config.cloud_api_key) or auth.load_tokens() is not None

    interactive = _is_interactive_session() if is_interactive is None else is_interactive

    # Trigger: environment-level promo suppression or non-interactive execution.
    # Why: avoid polluting scripts/CI output and support a hard opt-out.
    # Outcome: skip all promo copy for this invocation.
    if _promos_disabled_by_env() or not interactive:
        return

    # Trigger: command context where cloud promo is not actionable.
    # Why: mcp/stdin protocol and root help flows should stay noise-free.
    # Outcome: command continues without promo messaging.
    if invoked_subcommand in {None, "mcp"}:
        return

    if has_cloud_access or config.cloud_promo_opt_out:
        return

    show_first_run = not config.cloud_promo_first_run_shown
    show_version_notice = config.cloud_promo_last_version_shown != basic_memory.__version__
    if not show_first_run and not show_version_notice:
        return

    out = console or Console()
    out.print(
        Panel(
            _build_cloud_promo_message(),
            title="Basic Memory Cloud",
            border_style="cyan",
            expand=False,
        )
    )
    out.print(f"Learn more at [link={CLOUD_LEARN_MORE_URL}]{CLOUD_LEARN_MORE_URL}[/link]")
    out.print("[dim]Disable with: bm cloud promo --off[/dim]")

    config.cloud_promo_first_run_shown = True
    config.cloud_promo_last_version_shown = basic_memory.__version__
    manager.save_config(config)
