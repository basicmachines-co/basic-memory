"""Shared fixtures for schema integration tests.

Helper functions and path constants live in helpers.py for explicit import.
This file only contains pytest fixtures (auto-injected by pytest).
"""

from pathlib import Path

import pytest

from test_schema.helpers import (
    SCHEMAS_DIR,
    VALID_DIR,
    WARNINGS_DIR,
    EDGE_CASES_DIR,
    INFERENCE_DIR,
    DRIFT_SCHEMA_DIR,
    DRIFT_PEOPLE_DIR,
    parse_frontmatter,
)


@pytest.fixture
def schemas_dir() -> Path:
    return SCHEMAS_DIR


@pytest.fixture
def valid_dir() -> Path:
    return VALID_DIR


@pytest.fixture
def warnings_dir() -> Path:
    return WARNINGS_DIR


@pytest.fixture
def edge_cases_dir() -> Path:
    return EDGE_CASES_DIR


@pytest.fixture
def inference_dir() -> Path:
    return INFERENCE_DIR


@pytest.fixture
def drift_schema_dir() -> Path:
    return DRIFT_SCHEMA_DIR


@pytest.fixture
def drift_people_dir() -> Path:
    return DRIFT_PEOPLE_DIR


@pytest.fixture
def person_schema_frontmatter(schemas_dir) -> dict:
    """Load Person schema frontmatter from fixture."""
    return parse_frontmatter(schemas_dir / "Person.md")


@pytest.fixture
def book_schema_frontmatter(schemas_dir) -> dict:
    """Load Book schema frontmatter from fixture."""
    return parse_frontmatter(schemas_dir / "Book.md")


@pytest.fixture
def meeting_schema_frontmatter(schemas_dir) -> dict:
    """Load Meeting schema frontmatter from fixture."""
    return parse_frontmatter(schemas_dir / "Meeting.md")


@pytest.fixture
def software_project_schema_frontmatter(schemas_dir) -> dict:
    """Load SoftwareProject schema frontmatter from fixture."""
    return parse_frontmatter(schemas_dir / "SoftwareProject.md")


@pytest.fixture
def strict_schema_frontmatter(schemas_dir) -> dict:
    """Load StrictSchema frontmatter from fixture."""
    return parse_frontmatter(schemas_dir / "StrictSchema.md")
