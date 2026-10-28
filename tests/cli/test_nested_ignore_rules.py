"""Directory rules match anchored relative paths, not individual components (#1913)."""

from pathlib import Path

import pytest

from basic_memory.ignore_utils import filter_files


@pytest.mark.parametrize(
    ("pattern", "path", "ignored"),
    [
        ("/private/keys/", "private/keys/token.md", True),
        ("private/keys/", "private/keys/token.md", True),
        ("/private/*/", "private/team/token.md", True),
        ("private/*/", "private/team/token.md", True),
        ("/private/keys/", "other/private/keys/token.md", False),
        ("private/keys/", "other/private/keys/token.md", False),
        ("/private/keys/", "private/keys.md", False),
        ("private/keys/", "private/keys-other/token.md", False),
        ("keys/", "other/private/keys/token.md", True),
        ("/private/keys/", "private/keys/nested/token.md", True),
        ("private/*/", "private/file.md", False),
        ("keys/", "keys", False),
    ],
)
def test_nested_directory_ignore_rule(tmp_path: Path, pattern: str, path: str, ignored: bool):
    source = tmp_path / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("private data")
    selected, count = filter_files([source], tmp_path, {pattern})
    assert selected == ([] if ignored else [source])
    assert count == int(ignored)


@pytest.mark.parametrize(
    ("pattern", "path", "ignored"),
    [
        ("*/bar/", "foo/bar/note.md", True),
        ("*/bar/", "foo/a/bar/note.md", False),
        ("foo/**/bar/", "foo/bar/note.md", True),
        ("foo/**/bar/", "foo/a/bar/note.md", True),
        ("foo/**/bar/", "foo/a/b/bar/note.md", True),
        ("foo/**/bar/", "foo/a/b/barley/note.md", False),
        ("foo/**/", "foo/a.md", False),
        ("foo/**/", "foo/sub/a.md", True),
        ("/**/", "a.md", False),
        ("/**/", "sub/a.md", True),
    ],
)
def test_directory_wildcards_respect_components(
    tmp_path: Path, pattern: str, path: str, ignored: bool
):
    source = tmp_path / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("private data")
    selected, count = filter_files([source], tmp_path, {pattern})
    assert selected == ([] if ignored else [source])
    assert count == int(ignored)
