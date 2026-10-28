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
