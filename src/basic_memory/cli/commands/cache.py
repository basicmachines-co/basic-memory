"""Manage the optional standalone Redis read cache."""

import typer
from rich.console import Console

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.config import ConfigManager

console = Console()

cache_app = typer.Typer(help="Manage the Redis read cache.")
app.add_typer(cache_app, name="cache")


@cache_app.command("reset")
def reset() -> None:
    """Retire every cached read, for example after an upgrade changes API responses.

    Entries are not deleted; they become unreachable and expire on their own.
    Only applies when `redis_url` is configured.
    """
    config = ConfigManager().config
    if not config.redis_url:
        console.print("[yellow]No redis_url configured; there is no read cache to reset.[/yellow]")
        raise typer.Exit(0)
    run_with_cleanup(_reset(config.redis_url, max_connections=config.redis_max_connections))


async def _reset(redis_url: str, *, max_connections: int) -> None:
    # Deferred: the Redis client loads only when a reset actually runs (#886).
    from basic_memory.read_cache.lifecycle import open_redis_read_cache

    async with open_redis_read_cache(redis_url, max_connections=max_connections) as cache:
        assert cache is not None
        await cache.reset_all()
    console.print("[green]Read cache reset.[/green]")
