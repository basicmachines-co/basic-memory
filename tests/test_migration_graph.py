"""The migration graph must have exactly one head.

Two PRs that each add a migration off the same parent are each green on their
own and leave main with two heads once both merge. The suite builds schemas with
`create_all` and stamps them, so nothing here ran `upgrade head` against the
merged graph; the first fresh database on main failed to initialize instead
(#1440 + #1444, repaired by the `y8f9a0b1c2d3` merge revision).

This reads the graph the same way `run_migrations` does, so the check fails in
CI on the second PR to merge rather than on the next user's first `bm` command.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import basic_memory


def _script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(Path(basic_memory.__file__).parent / "alembic"))
    return ScriptDirectory.from_config(config)


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
