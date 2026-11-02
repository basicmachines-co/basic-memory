"""The conflict keys stored on every entity row for file path conflict checks."""

import pytest

from basic_memory.utils import file_path_conflict_keys, generate_permalink


@pytest.mark.parametrize(
    ("file_path", "equivalent_path"),
    [
        ("notes/foo.md", "Notes/FOO.md"),  # case only
        ("notes/café.md", "notes/café.md"),  # NFC vs NFD
        ("notes/foo.md", "notes\\foo.md"),  # path separator
    ],
)
def test_equivalent_paths_share_a_path_key(file_path: str, equivalent_path: str) -> None:
    assert file_path != equivalent_path
    assert (
        file_path_conflict_keys(file_path).path_key
        == file_path_conflict_keys(equivalent_path).path_key
    )


def test_paths_that_generate_one_permalink_share_a_permalink_key() -> None:
    first = file_path_conflict_keys("notes/Foo Bar.md")
    second = file_path_conflict_keys("notes/foo_bar.md")

    assert first.path_key != second.path_key
    assert first.permalink == second.permalink == "notes/foo-bar"


def test_permalink_key_is_the_generated_permalink_for_any_file_type() -> None:
    for file_path in ("notes/Foo Bar.md", "files/My Image.png", "data/report.v2.csv"):
        assert file_path_conflict_keys(file_path).permalink == generate_permalink(file_path)
