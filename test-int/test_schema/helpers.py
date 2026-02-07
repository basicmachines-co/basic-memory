"""Shared helpers and constants for schema integration tests.

Separated from conftest.py so they can be explicitly imported by test modules.
Conftest fixtures are auto-injected by pytest and don't need explicit import.
"""

import re
from pathlib import Path

import yaml


# --- Fixture Paths ---

FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "schema"
SCHEMAS_DIR = FIXTURES_ROOT / "schemas"
VALID_DIR = FIXTURES_ROOT / "valid"
WARNINGS_DIR = FIXTURES_ROOT / "warnings"
EDGE_CASES_DIR = FIXTURES_ROOT / "edge-cases"
INFERENCE_DIR = FIXTURES_ROOT / "inference" / "people"
DRIFT_SCHEMA_DIR = FIXTURES_ROOT / "drift" / "schema"
DRIFT_PEOPLE_DIR = FIXTURES_ROOT / "drift" / "people"


# --- Frontmatter Parsing ---


def parse_frontmatter(filepath: Path) -> dict:
    """Extract YAML frontmatter from a markdown file."""
    text = filepath.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1)) or {}


def parse_observations(filepath: Path) -> list[tuple[str, str]]:
    """Extract observation tuples from a markdown file.

    Parses lines matching: - [category] content
    """
    text = filepath.read_text(encoding="utf-8")
    pattern = re.compile(r"^- \[([^\]]+)\] (.+)$", re.MULTILINE)
    return [(m.group(1), m.group(2)) for m in pattern.finditer(text)]


def parse_relations(filepath: Path) -> list[tuple[str, str]]:
    """Extract relation tuples from a markdown file.

    Parses lines matching: - relation_type [[Target]]
    Only matches lines under a ## Relations heading.
    """
    text = filepath.read_text(encoding="utf-8")
    relations_match = re.search(r"## Relations\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not relations_match:
        return []
    relations_text = relations_match.group(1)
    pattern = re.compile(r"^- (\S+) \[\[([^\]]+)\]\]$", re.MULTILINE)
    return [(m.group(1), m.group(2)) for m in pattern.finditer(relations_text)]
