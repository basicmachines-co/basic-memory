"""The distributed Basic Memory skills, served over MCP.

Each skill the wheel ships under ``basic_memory/data/skills`` is readable as
``skill://<name>/SKILL.md`` (plus its supporting files), so any client that reads
MCP resources can load it without installing anything. Clients that negotiate the
Skills extension (SEP-2640, ``io.modelcontextprotocol/skills``) can also call
``skills/list`` for each skill's description and a digest manifest of its files.

The skill set is the release set: pyproject's force-include list decides what lands
in ``basic_memory/data/skills``, and tests/test_skills_dist.py pins it.
"""

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from fastmcp.skills.provider import SkillsDirectoryProvider

from basic_memory.mcp.server import mcp

SKILLS_RESOURCE_PATH = "basic_memory/data/skills"


def distributed_skills_root() -> Path:
    """Locate the skills the installed distribution ships.

    Both the wheel and the editable install materialize force-included files on
    disk, so this resolves to a real directory in every supported install. An
    editable install copies them at install time: edits under `skills/` show up
    here only after a reinstall.
    """
    # Trigger: no package metadata (a `PYTHONPATH=src` run, a vendored copy).
    # Why: the skills exist only as force-included files of an install.
    # Outcome: the server fails at import with the same message as a missing directory.
    try:
        root = distribution("basic-memory").locate_file(SKILLS_RESOURCE_PATH)
    except PackageNotFoundError as error:  # pragma: no cover
        raise RuntimeError(
            "Basic Memory skills are missing: basic-memory is not installed"
        ) from error
    # Trigger: a zipped or partial install. Why: an empty provider would silently
    # advertise no skills. Outcome: the server fails at import instead.
    if not isinstance(root, Path) or not root.is_dir():  # pragma: no cover
        raise RuntimeError(f"Basic Memory skills are missing from the install: {root}")
    return root


# The provider validates every SKILL.md at construction, so a malformed skill fails
# the server at import, and it registers the Skills extension (skills/list) itself.
skills_provider = SkillsDirectoryProvider(distributed_skills_root())
mcp.add_provider(skills_provider)
