"""Functions for managing database migrations."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.operations.ops import MigrationScript
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from loguru import logger

# --- Sequential revision IDs ---
# Revisions up to and including this one carry unnumbered IDs: some are random hex from
# Alembic, many were hand-picked (z9a0b1c2d3e4 itself is not hex). Every revision after it
# is named by its schema version, zero-padded to four digits: "0040" revises
# "z9a0b1c2d3e4", "0041" revises "0040". A version number orders schemas at a glance
# (cloud tenants report which one they run), where an unnumbered ID only says "different".
# The padding keeps migration files in version order in a directory listing.
LAST_UNNUMBERED_REVISION = "z9a0b1c2d3e4"
REVISION_ID_DIGITS = 4


def get_alembic_config() -> Config:  # pragma: no cover
    """Get alembic config with correct paths."""
    migrations_path = Path(__file__).parent
    alembic_ini = migrations_path / "alembic.ini"

    config = Config(alembic_ini)
    config.set_main_option("script_location", str(migrations_path))
    return config


def get_script_directory() -> ScriptDirectory:
    """Read the migration graph shipped with this package."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent))
    return ScriptDirectory.from_config(config)


def schema_version(revision: str, script: ScriptDirectory | None = None) -> int:
    """Return the schema version a revision produces: how many revisions it applies.

    The count is taken from the graph, so the unnumbered revisions get a number too
    (z9a0b1c2d3e4 is 39). For sequential revisions it equals int(revision): "0040" is 40.
    """
    script = script or get_script_directory()

    # Trigger: Alembic resolves unique prefixes ("z9a" finds z9a0b1c2d3e4).
    # Why: a version must name exactly one revision; a prefix that happens to resolve
    # today becomes ambiguous when a later revision shares it.
    # Outcome: only a full, known revision ID gets a version.
    if revision not in {known.revision for known in script.walk_revisions()}:
        raise ValueError(f"unknown alembic revision: {revision!r}")

    return sum(1 for _ in script.walk_revisions("base", revision))


def next_revision_id(script: ScriptDirectory) -> str:
    """Return the ID for a new revision on top of the single current head."""
    heads = script.get_heads()
    if len(heads) != 1:
        raise ValueError(
            f"alembic has {len(heads)} heads {sorted(heads)}; join them with a merge "
            "revision before adding a new one"
        )
    return f"{schema_version(heads[0], script) + 1:0{REVISION_ID_DIGITS}d}"


def assign_sequential_revision_ids(
    context: MigrationContext,
    revision: object,
    directives: list[MigrationScript],
) -> None:
    """env.py `process_revision_directives` hook: name each new revision by its version."""
    # Trigger: Alembic always hands the hook a context built from the script directory.
    # Why: without the graph there is no head to count from; guessing an ID would
    # silently fork the history.
    # Outcome: fail fast instead of writing a revision with an unchecked ID.
    if context.script is None:
        raise RuntimeError("alembic revision context has no script directory")

    for directive in directives:
        directive.rev_id = next_revision_id(context.script)


def reset_database():  # pragma: no cover
    """Drop and recreate all tables."""
    logger.info("Resetting database...")
    config = get_alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
