"""Tests for basic-memory package"""

import sys
import tomllib

import pytest
from frontmatter.default_handlers import toml

from basic_memory import __version__
from basic_memory.config import config


def read_toml_version(file_path):
    try:
        with open(file_path, "rb") as f:
            if sys.version_info >= (3, 11):
                data = tomllib.load(f)
            else:
                data = toml.load(f)
        if "project" in data and "version" in data["project"]:
            return data["project"]["version"]
        else:
            return None
    except FileNotFoundError:
        return None
    except (toml.TomlDecodeError, tomllib.TOMLDecodeError):
        return None


file_path = "pyproject.toml"
version = read_toml_version(file_path)


def test_version():
    """Test version is set in project src code and pyproject.toml"""
    assert __version__ == version


def test_config_env():
    """Test the config env is set to test for pytest"""
    assert config.env == "test"


@pytest.mark.asyncio
async def test_config_env_async():
    """Test the config env is set to test for async pytest"""
    assert config.env == "test"
