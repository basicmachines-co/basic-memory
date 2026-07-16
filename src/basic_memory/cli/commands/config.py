"""CLI commands for managing Basic Memory's config.json (issue #991).

Every user-facing config option previously required hand-editing
``config.json`` or knowing the ``BASIC_MEMORY_*`` env-var naming convention.
This module exposes ``bm config list|get|set|unset`` for the scalar subset of
``BasicMemoryConfig`` fields, validating ``set`` through the model itself so
invalid values fail with the same Pydantic error a malformed config.json
would produce.
"""

import json
import os
import types
import typing
from enum import Enum
from typing import Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from basic_memory.cli.app import app
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.redaction import SECRET_FIELDS, URL_FIELDS, redact_url

console = Console()

config_app = typer.Typer(help="Manage Basic Memory's config.json settings")
app.add_typer(config_app, name="config")

_SCALAR_TYPES = (str, int, float, bool)
_UNION_ORIGINS = (typing.Union, types.UnionType)


def _is_scalar_annotation(annotation: Any) -> bool:
    """Whether a field's type annotation is a plain scalar (or Optional/Literal/Enum of one).

    Excludes structured fields (dict, list, nested models, datetime) that need
    their own dedicated commands (e.g. `projects` -> `bm project add`) or
    richer input parsing than a single CLI string argument can offer.
    """
    origin = typing.get_origin(annotation)
    if origin in _UNION_ORIGINS:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return len(args) == 1 and _is_scalar_annotation(args[0])
    if origin is typing.Literal:
        return True
    if origin is not None:
        return False
    return isinstance(annotation, type) and issubclass(annotation, (*_SCALAR_TYPES, Enum))


def _configurable_fields() -> tuple[str, ...]:
    """Scalar BasicMemoryConfig fields settable via `bm config`, derived from the model."""
    return tuple(
        sorted(
            name
            for name, field in BasicMemoryConfig.model_fields.items()
            if _is_scalar_annotation(field.annotation)
        )
    )


# Derived at import time so the allowlist tracks BasicMemoryConfig automatically
# rather than drifting from a hand-maintained list.
CONFIGURABLE_FIELDS: tuple[str, ...] = _configurable_fields()


def _env_var_name(key: str) -> str:
    return f"BASIC_MEMORY_{key.upper()}"


def _mask(key: str, raw: str) -> str:
    """Apply the same secret/URL redaction rules as `basic_memory_diagnostics` (#963)."""
    if key in SECRET_FIELDS:
        return "********"
    if key in URL_FIELDS:
        return redact_url(raw)
    return raw


def _display_value(key: str, value: Any) -> str:
    """Render an effective config value for display, masking secrets/URL credentials."""
    if value is None:
        return "(not set)"
    if key in SECRET_FIELDS:
        return "********"
    if isinstance(value, Enum):
        value = value.value
    return _mask(key, str(value))


def _unknown_key_error(key: str) -> None:
    console.print(f"[red]Error: '{key}' is not a recognized setting.[/red]")
    console.print("[dim]Run 'bm config list' to see all available settings.[/dim]")


def _require_known_key(key: str) -> None:
    if key not in CONFIGURABLE_FIELDS:
        _unknown_key_error(key)
        raise typer.Exit(1)


@config_app.command("list")
def config_list(
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
) -> None:
    """List every configurable setting with its effective value and source.

    Source is one of: default (unset), file (set in config.json), or env
    (overridden by a BASIC_MEMORY_* environment variable, which always wins).
    """
    config_manager = ConfigManager()
    config = config_manager.config

    raw_file_data: dict = {}
    if config_manager.config_file.exists():
        try:
            raw_file_data = json.loads(config_manager.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - config.json validated on load
            raw_file_data = {}

    rows = []
    for key in CONFIGURABLE_FIELDS:
        value = getattr(config, key)
        env_var = _env_var_name(key)
        if env_var in os.environ:
            source = f"env ({env_var})"
        elif key in raw_file_data:
            source = "file"
        else:
            source = "default"
        rows.append({"key": key, "value": _display_value(key, value), "source": source})

    if json_output:
        print(json.dumps(rows, indent=2))
        return

    table = Table(title="Basic Memory Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    table.add_column("Source", style="yellow")
    for row in rows:
        table.add_row(row["key"], row["value"], row["source"])
    console.print(table)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config setting name (see 'bm config list')"),
) -> None:
    """Show the effective value of one config setting."""
    _require_known_key(key)

    config = ConfigManager().config
    value = getattr(config, key)
    console.print(f"{key} = {_display_value(key, value)}")

    env_var = _env_var_name(key)
    if env_var in os.environ:
        env_value = _mask(key, os.environ[env_var])
        console.print(f"[yellow]Overridden by ${env_var} = {env_value}[/yellow]")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config setting name (see 'bm config list')"),
    value: str = typer.Argument(..., help="New value"),
) -> None:
    """Set a config value, validated through BasicMemoryConfig before writing.

    Invalid values (e.g. `cli_output_style` outside rich|plain) fail with the
    Pydantic validation error instead of being written to config.json.
    """
    _require_known_key(key)

    config_manager = ConfigManager()
    config = config_manager.load_config()

    candidate = config.model_dump(mode="json")
    candidate[key] = value

    try:
        validated = BasicMemoryConfig.model_validate(candidate)
    except ValidationError as e:
        console.print(f"[red]Error: invalid value for '{key}':[/red]")
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    setattr(config, key, getattr(validated, key))
    config_manager.save_config(config)

    console.print(f"[green]{key} = {_display_value(key, getattr(config, key))}[/green]")

    env_var = _env_var_name(key)
    if env_var in os.environ:
        console.print(
            f"[yellow]Note: ${env_var} is set and will override this file value "
            "until the environment variable is unset.[/yellow]"
        )


@config_app.command("unset")
def config_unset(
    key: str = typer.Argument(..., help="Config setting name (see 'bm config list')"),
) -> None:
    """Revert a config setting to its default value."""
    _require_known_key(key)

    config_manager = ConfigManager()
    config = config_manager.load_config()

    default_value = BasicMemoryConfig.model_fields[key].get_default(call_default_factory=True)
    setattr(config, key, default_value)
    config_manager.save_config(config)

    console.print(f"[green]{key} reverted to default: {_display_value(key, default_value)}[/green]")
