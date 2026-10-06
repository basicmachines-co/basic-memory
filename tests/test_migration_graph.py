"""The migration graph must have exactly one head.

Two PRs that each add a migration off the same parent are each green on their
own and leave main with two heads once both merge. The suite builds schemas with
`create_all` and stamps them, so nothing here ran `upgrade head` against the
merged graph; the first fresh database on main failed to initialize instead
(#1440 + #1444, repaired by the `y8f9a0b1c2d3` merge revision).

This reads the graph the same way `run_migrations` does, so the check fails in
CI on the second PR to merge rather than on the next user's first `bm` command.

Revisions after LAST_HEX_REVISION are named by schema version ("0040", "0041", ...), so the
graph is also checked for a contiguous, linear numeric tail.
"""

import re

import pytest
from alembic.config import Config
from alembic.operations.ops import DowngradeOps, MigrationScript, UpgradeOps
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from basic_memory.alembic.migrations import (
    LAST_HEX_REVISION,
    REVISION_ID_DIGITS,
    assign_sequential_revision_ids,
    get_script_directory,
    next_revision_id,
    schema_version,
)

_script_directory = get_script_directory


def test_the_migration_graph_has_one_head():
    heads = _script_directory().get_heads()

    assert len(heads) == 1, (
        f"alembic has {len(heads)} heads {sorted(heads)}; `upgrade head` refuses to run "
        "until they are joined by a merge revision (down_revision = (a, b))"
    )


def test_every_revision_is_reachable_from_the_head():
    script = _script_directory()
    (head,) = script.get_heads()

    reachable = {rev.revision for rev in script.walk_revisions("base", head)}
    all_revisions = {rev.revision for rev in script.walk_revisions()}

    assert reachable == all_revisions, (
        f"revisions not on the path from base to {head}: {sorted(all_revisions - reachable)}"
    )


def test_revisions_after_the_last_hex_one_are_sequential():
    script = _script_directory()
    (head,) = script.get_heads()

    # walk_revisions goes head -> base; reverse it so versions count upward.
    chain = list(reversed(list(script.walk_revisions("base", head))))
    hex_end = [rev.revision for rev in chain].index(LAST_HEX_REVISION)

    previous = LAST_HEX_REVISION
    for expected, rev in enumerate(chain[hex_end + 1 :], start=schema_version(previous) + 1):
        assert re.fullmatch(r"\d{4}", rev.revision), (
            f"revision {rev.revision!r} must be exactly four digits"
        )
        assert int(rev.revision) == expected, (
            f"revision {rev.revision!r} should be named {expected:04d}; "
            "generate migrations with `just migration` so the ID is assigned"
        )
        assert rev.down_revision == previous, (
            f"revision {rev.revision} revises {rev.down_revision!r}, expected {previous!r}"
        )
        previous = rev.revision


def test_schema_version_counts_applied_revisions():
    assert schema_version(LAST_HEX_REVISION) == 39
    assert schema_version("3dae7c7b1564") == 1  # initial schema


@pytest.mark.parametrize("revision", ["nope", "z9a", "999", "0999", ""])
def test_schema_version_rejects_unknown_revisions(revision):
    # "z9a" is a prefix Alembic itself would resolve; a version must name one revision.
    with pytest.raises(ValueError, match="unknown alembic revision"):
        schema_version(revision)


def test_next_revision_id_follows_the_head():
    script = _script_directory()
    (head,) = script.get_heads()

    next_id = next_revision_id(script)

    assert len(next_id) == REVISION_ID_DIGITS
    assert int(next_id) == schema_version(head) + 1


def test_next_revision_id_refuses_a_forked_graph(tmp_path):
    # Two roots means two heads; numbering on top of either would hide the fork.
    versions = tmp_path / "versions"
    versions.mkdir()
    for revision in ("a1", "b2"):
        (versions / f"{revision}_root.py").write_text(
            f"revision = {revision!r}\ndown_revision = None\n"
            "def upgrade():\n    pass\n\ndef downgrade():\n    pass\n"
        )
    config = Config()
    config.set_main_option("script_location", str(tmp_path))

    with pytest.raises(ValueError, match="2 heads"):
        next_revision_id(ScriptDirectory.from_config(config))


def test_revision_hook_assigns_the_next_version():
    script = _script_directory()
    context = MigrationContext.configure(dialect_name="sqlite", opts={"script": script})
    directive = MigrationScript(
        rev_id="abc123", upgrade_ops=UpgradeOps(), downgrade_ops=DowngradeOps()
    )

    assign_sequential_revision_ids(context, "head", [directive])

    assert directive.rev_id == next_revision_id(script)


def test_revision_hook_requires_a_script_directory():
    context = MigrationContext.configure(dialect_name="sqlite")
    directive = MigrationScript(rev_id=None, upgrade_ops=UpgradeOps(), downgrade_ops=DowngradeOps())

    with pytest.raises(RuntimeError, match="no script directory"):
        assign_sequential_revision_ids(context, "head", [directive])
