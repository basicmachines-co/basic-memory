"""Literal filename validation retains containment syntax restrictions."""

import pytest

from basic_memory.utils import valid_project_path_value


@pytest.mark.parametrize("path", ["a:note.md", "a:", "a:/note.md"])
def test_literal_colons_are_an_explicit_filename_policy(path: str):
    assert not valid_project_path_value(path)
    assert valid_project_path_value(path, allow_literal_colon=True)


@pytest.mark.parametrize("path", ["a:/../outside.md", "/outside.md", "\\outside.md", "a:\n"])
def test_literal_colons_do_not_allow_escape_or_controls(path: str):
    assert not valid_project_path_value(path, allow_literal_colon=True)
