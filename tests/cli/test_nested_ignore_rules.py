"""Directory rules match anchored relative paths, not individual components (#1913)."""

from cProfile import Profile
from pathlib import Path
from types import CodeType

import pytest

from basic_memory.ignore_utils import filter_files, load_gitignore_patterns


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
        ("#cache/", "#cache/note.md", True),
        ("!cache/", "!cache/note.md", True),
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
        ("foo/***/bar/", "foo/bar/note.md", True),
        ("foo/***/bar/", "foo/a/b/bar/note.md", True),
        ("foo/****/bar/", "foo/bar/note.md", True),
        ("foo/****/", "foo/note.md", False),
        ("foo/****/", "foo/a/note.md", True),
        ("foo/**a/bar/", "foo/a/b/bar/note.md", False),
        ("a/[^x]/", "a/x/note.md", False),
        ("a/[^x]/", "a/y/note.md", True),
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


@pytest.mark.parametrize("ignore_file", [".bmignore", ".gitignore"])
@pytest.mark.parametrize(
    ("pattern", "directory"),
    [
        (r"\#cache/", "#cache"),
        (r"\!cache/", "!cache"),
        (r"cache\ space/", "cache space"),
        (r"notes/\[cache\]/", "notes/[cache]"),
    ],
)
def test_directory_rule_loader_preserves_git_escapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ignore_file: str,
    pattern: str,
    directory: str,
):
    config = tmp_path / "config"
    config.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config))
    ((config if ignore_file == ".bmignore" else root) / ignore_file).write_text(pattern + "\n")
    source = root / directory / "note.md"
    source.parent.mkdir(parents=True)
    source.write_text("private content")
    rules = load_gitignore_patterns(root, strict=True)
    assert filter_files([source], root, rules) == ([], 1)


def test_large_ignore_set_compiles_once_across_files(tmp_path: Path):
    rules = {f"excluded-{index}/" for index in range(513)}
    files = [tmp_path / f"note-{index}.md" for index in range(3)]
    for path in files:
        path.write_text("note")
    # Count real pathspec compilation calls without replacing its implementation
    # or depending on elapsed time on a busy CI runner.
    with Profile() as profile:
        assert filter_files(files, tmp_path, rules) == (files, 0)
    compilations = sum(
        entry.callcount
        for entry in profile.getstats()
        if isinstance(entry.code, CodeType)
        and entry.code.co_qualname == "RegexPattern.__init__"
        and Path(entry.code.co_filename).name == "pattern.py"
        and "pathspec" in Path(entry.code.co_filename).parts
    )
    assert compilations <= len(rules)


@pytest.mark.parametrize("invalid", [r"foo\//", "a/[z-a]/"])
@pytest.mark.parametrize(
    ("valid", "relative"), [("private/", "private/secret.md"), ("*.secret", "note.secret")]
)
def test_invalid_directory_rule_cannot_disable_exclusions(
    tmp_path: Path, invalid: str, valid: str, relative: str
):
    source = tmp_path / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("private content")
    assert filter_files([source], tmp_path, {invalid, valid}) == ([], 1)
