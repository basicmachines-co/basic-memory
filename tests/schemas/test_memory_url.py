"""Tests for MemoryUrl parsing."""

import pytest
from basic_memory.schemas.memory import MemoryUrl, memory_url, memory_url_path


def test_basic_permalink():
    """Test basic permalink parsing."""
    url = memory_url.validate_strings("memory://specs/search")
    assert str(url) == "memory://specs/search"
    assert memory_url_path(url) == "specs/search"


def test_glob_pattern():
    """Test pattern matching."""
    url = memory_url.validate_python("memory://specs/search/*")
    assert memory_url_path(url) == "specs/search/*"


def test_related_prefix():
    """Test related content prefix."""
    url = memory_url.validate_python("memory://related/specs/search")
    assert memory_url_path(url) == "related/specs/search"


def test_context_prefix():
    """Test context prefix."""
    url = memory_url.validate_python("memory://context/current")
    assert memory_url_path(url) == "context/current"


def test_complex_pattern():
    """Test multiple glob patterns."""
    url = memory_url.validate_python("memory://specs/*/search/*")
    assert memory_url_path(url) == "specs/*/search/*"


def test_path_with_dashes():
    """Test path with dashes and other chars."""
    url = memory_url.validate_python("memory://file-sync-and-note-updates-implementation")
    assert memory_url_path(url) == "file-sync-and-note-updates-implementation"


def test_str_representation():
    """Test converting back to string."""
    url = memory_url.validate_python("memory://specs/search")
    assert url == "memory://specs/search"
