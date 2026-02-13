"""Cloud promo messaging for CLI entrypoint."""

import os
import sys
from collections.abc import Callable

import typer

from basic_memory.config import ConfigManager

CLOUD_PROMO_VERSION = "2026-02-06"
OSS_DISCOUNT_CODE = "{{OSS_DISCOUNT_CODE}}"


def _promos_disabled_by_env() -> bool:
    """Check environment-level kill switch for promo output."""
    value = os.getenv("BASIC_MEMORY_NO_PROMOS", "").strip().lower()
    return value in {"1", "true", "yes"}


def _is_interactive_session() -> bool:
    """Return whether stdin/stdout are interactive terminals."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _build_first_run_message() -> str:
    """Build first-run cloud promo copy."""
    return (
        "Basic Memory initialized (local mode).\n"
        "Cloud is optional and keeps your workflow local-first.\n"
        "Cloud adds cross-device sync + mobile/web access.\n"
        f"OSS discount: {OSS_DISCOUNT_CODE} (20% off for 3 months).\n"
        "Run `bm cloud login` to enable."
    )


def _build_version_message() -> str:
    """Build cloud promo copy shown after promo-version bumps."""
    return (
        "New in Basic Memory Cloud: cross-device sync + mobile/web access.\n"
        f"OSS discount: {OSS_DISCOUNT_CODE} (20% off for 3 months).\n"
        "Run `bm cloud login` to enable."
    )


def maybe_show_cloud_promo(
    invoked_subcommand: str | None,
    *,
    config_manager: ConfigManager | None = None,
    is_interactive: bool | None = None,
    echo: Callable[[str], None] = typer.echo,
) -> None:
    """Show cloud promo copy when discovery gates are satisfied."""
    manager = config_manager or ConfigManager()
    config = manager.load_config()

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

    if config.cloud_mode_enabled or config.cloud_promo_opt_out:
        return

    show_first_run = not config.cloud_promo_first_run_shown
    show_version_notice = config.cloud_promo_last_version_shown != CLOUD_PROMO_VERSION
    if not show_first_run and not show_version_notice:
        return

    message = _build_first_run_message() if show_first_run else _build_version_message()
    echo(message)

    config.cloud_promo_first_run_shown = True
    config.cloud_promo_last_version_shown = CLOUD_PROMO_VERSION
    manager.save_config(config)
