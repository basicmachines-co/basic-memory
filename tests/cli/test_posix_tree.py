"""`bm tree` rebuilds a hierarchy from bm_find's flat listing."""

import pytest

from basic_memory.cli.commands.posix import _build_tree, _listing_root, _TreeEntry


def node(path: str, kind: str) -> dict[str, str]:
    return {"directory_path": path, "type": kind}


def test_files_without_their_folder_synthesize_it():
    """A glob returns files only; their folders are drawn anyway."""
    tree = _build_tree([node("/specs/auth/deep.md", "file")], "/")

    assert tree == {
        "specs": _TreeEntry(
            is_dir=True,
            children={"auth": _TreeEntry(is_dir=True, children={"deep.md": _TreeEntry(False)})},
        )
    }


def test_a_folder_listed_after_its_file_stays_one_folder():
    tree = _build_tree([node("/specs/search.md", "file"), node("/specs", "directory")], "/")

    assert list(tree) == ["specs"]
    assert tree["specs"].is_dir is True
    assert list(tree["specs"].children) == ["search.md"]


def test_the_root_is_not_its_own_child():
    tree = _build_tree([node("/specs", "directory"), node("/specs/intro.md", "file")], "/specs")

    assert tree == {"intro.md": _TreeEntry(False)}


@pytest.mark.parametrize(
    ("path", "project", "root"),
    [
        # A path that starts with the project's name answers in its permalink.
        ("My Research/specs", "My Research", "my-research/specs"),
        ("/My Research/specs/", "My Research", "my-research/specs"),
        ("My Research", "My Research", "my-research"),
        # Any other path, or no project, comes back as typed.
        ("specs", "My Research", "specs"),
        ("/", "My Research", "/"),
        ("My Research/specs", None, "My Research/specs"),
    ],
)
def test_listing_root_uses_the_spelling_bm_find_answers_in(path, project, root):
    assert _listing_root(path, project) == root
