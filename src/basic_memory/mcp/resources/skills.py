"""The distributed Basic Memory skills, served over MCP.

Each skill the wheel ships under ``basic_memory/data/skills`` is readable as
``skill://<name>/SKILL.md`` (plus its supporting files), so any client that reads
MCP resources can load it without installing anything. Clients that negotiate the
Skills extension (SEP-2640, ``io.modelcontextprotocol/skills``) can also call
``skills/list`` for each skill's description and a digest manifest of its files.

The skill set is the release set: pyproject's force-include list decides what lands
in ``basic_memory/data/skills``, and tests/test_skills_dist.py pins it.
"""

from importlib.metadata import distribution
from pathlib import Path

from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.skills.extension import SkillsExtension

from basic_memory.mcp.server import mcp

SKILLS_RESOURCE_PATH = "basic_memory/data/skills"


def distributed_skills_root() -> Path:
    """Locate the skills the installed distribution ships.

    Both the wheel and the editable install materialize force-included files on
    disk, so this resolves to a real directory in every supported install.
    """
    root = distribution("basic-memory").locate_file(SKILLS_RESOURCE_PATH)
    # Trigger: a zipped or partial install. Why: an empty provider would silently
    # advertise no skills. Outcome: the server fails at import instead.
    if not isinstance(root, Path) or not root.is_dir():  # pragma: no cover
        raise RuntimeError(f"Basic Memory skills are missing from the install: {root}")
    return root


# experimental_skills switches the provider to SEP-2640 semantics: strict manifests
# and byte-preserving reads, which skills/get digests depend on.
skills_provider = SkillsDirectoryProvider(roots=distributed_skills_root(), experimental_skills=True)
mcp.add_provider(skills_provider)
mcp.add_extension(SkillsExtension(providers=[skills_provider]))
