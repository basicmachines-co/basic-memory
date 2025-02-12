"""Tests for basic-memory package"""

from basic_memory import __version__


def test_version():
    """Test version is set"""
    assert __version__ is not None
