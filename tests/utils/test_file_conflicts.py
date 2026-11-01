"""File conflict detection against stored permalinks."""

import random
import time

import pytest

from basic_memory import utils
from basic_memory.utils import (
    ExistingFilePermalink,
    build_canonical_permalink,
    detect_potential_file_conflicts,
    generate_permalink,
    normalize_file_path_for_comparison,
)


def reference_detect_conflicts(file_path: str, existing_paths: list[str]) -> list[str]:
    """The previous detector: regenerate a permalink for every existing path."""
    normalized_input = normalize_file_path_for_comparison(file_path)
    input_permalink = generate_permalink(file_path)
    conflicts = []
    for existing_path in existing_paths:
        if existing_path == file_path:
            continue
        if normalize_file_path_for_comparison(existing_path) == normalized_input:
            conflicts.append(existing_path)
            continue
        if generate_permalink(existing_path) == input_permalink:
            conflicts.append(existing_path)
    return conflicts


def stored(file_path: str, permalink: str | None) -> ExistingFilePermalink:
    return ExistingFilePermalink(file_path=file_path, permalink=permalink)


def project_canonical(path: str) -> str:
    return build_canonical_permalink("test-project", path)


# --- Path normalization rules are unchanged ---


@pytest.mark.parametrize(
    ("file_path", "existing_path"),
    [
        ("notes/foo.md", "Notes/FOO.md"),  # case only
        ("notes/café.md", "notes/café.md"),  # NFC vs NFD
        ("notes/foo.md", "notes\\foo.md"),  # path separator
    ],
)
@pytest.mark.parametrize("permalink", [None, "unrelated/custom-permalink"])
def test_equivalent_paths_conflict_whatever_permalink_is_stored(
    file_path: str, existing_path: str, permalink: str | None
) -> None:
    existing = [stored(existing_path, permalink)]

    assert detect_potential_file_conflicts(file_path, existing) == [existing_path]


def test_identical_path_is_not_a_conflict() -> None:
    existing = [stored("notes/foo.md", "notes/foo"), stored("files/a.png", None)]

    assert detect_potential_file_conflicts("notes/foo.md", existing) == []
    assert detect_potential_file_conflicts("files/a.png", existing) == []


# --- Stored permalinks and generated permalinks in one project ---


MIXED_PROJECT = [
    stored("notes/Foo Bar.md", "test-project/notes/foo-bar"),  # canonical, project prefixed
    stored("legacy/Old Note.md", "legacy/old-note"),  # stored before project prefixes
    stored("custom/Renamed.md", "custom/chosen-slug"),  # frontmatter permalink
    stored("moved/SKILL.md", "test-project/moved/song-sketching"),  # kept after a move
    stored("files/My Image.png", None),  # non-markdown, no stored permalink
]


@pytest.mark.parametrize(
    ("file_path", "expected"),
    [
        # Matches a stored project-qualified permalink through the canonical form.
        ("notes/foo_bar.md", ["notes/Foo Bar.md"]),
        # Matches a stored unprefixed permalink through the generated form.
        ("legacy/old_note.md", ["legacy/Old Note.md"]),
        # A file without a stored permalink is still compared by its generated permalink.
        ("files/my_image.png", ["files/My Image.png"]),
        ("notes/unrelated.md", []),
        # A stored permalink that no longer comes from its file's path does not block that
        # name: the new note gets a suffixed permalink, as before.
        ("custom/chosen_slug.md", []),
        ("moved/song-sketching.md", []),
    ],
)
def test_new_file_conflicts_with_stored_and_generated_permalinks(
    file_path: str, expected: list[str]
) -> None:
    conflicts = detect_potential_file_conflicts(
        file_path, MIXED_PROJECT, canonical_permalink=project_canonical
    )

    assert conflicts == expected


def test_path_inside_project_slug_conflicts_with_its_unprefixed_twin() -> None:
    """Both paths build the canonical permalink test-project/notes/foo.

    The previous detector compared only unprefixed generated permalinks and let the second
    note through with a suffixed permalink. Comparing canonical permalinks flags it.
    """
    existing = [stored("notes/foo.md", project_canonical("notes/foo.md"))]

    conflicts = detect_potential_file_conflicts(
        "test-project/notes/foo.md", existing, canonical_permalink=project_canonical
    )

    assert conflicts == ["notes/foo.md"]
    assert reference_detect_conflicts("test-project/notes/foo.md", ["notes/foo.md"]) == []


def test_conflicts_are_detected_in_both_directions() -> None:
    with_permalink = "notes/Foo Bar.md"
    without_permalink = "notes/foo_bar.md"
    project = [
        stored(with_permalink, project_canonical(with_permalink)),
        stored(without_permalink, None),
    ]

    def check(file_path: str) -> list[str]:
        return detect_potential_file_conflicts(
            file_path, project, canonical_permalink=project_canonical
        )

    # The file without a stored permalink sees the stored one, and the reverse.
    assert check(without_permalink) == [with_permalink]
    assert check(with_permalink) == [without_permalink]


def test_matches_previous_detector_when_stored_permalinks_come_from_paths() -> None:
    """With canonical stored permalinks, the result equals regenerating every permalink."""
    segments = ["notes", "Notes", "a b", "a_b", "a-b", "Café", "café", "中文", "v2.0", "x"]
    names = ["Foo Bar", "foo_bar", "foo-bar", "FOO", "测试文档", "Résumé", "API (v2)", "1.0.0"]
    extensions = [".md", ".MD", ".png", ""]
    randomizer = random.Random(20261101)
    paths = sorted(
        {
            "/".join(randomizer.sample(segments, randomizer.randint(0, 2)))
            + "/"
            + randomizer.choice(names)
            + randomizer.choice(extensions)
            for _ in range(400)
        }
    )
    for include_project in (True, False):
        project_slug = "test-project" if include_project else None
        existing = [
            stored(
                path,
                # Non-markdown files are indexed without a permalink.
                build_canonical_permalink(project_slug, path, include_project)
                if path.lower().endswith(".md")
                else None,
            )
            for path in paths
        ]

        def canonical(path: str) -> str:
            return build_canonical_permalink(project_slug, path, include_project)

        for file_path in paths:
            assert detect_potential_file_conflicts(
                file_path, existing, canonical_permalink=canonical
            ) == reference_detect_conflicts(file_path, paths), file_path


# --- Work no longer regenerates every stored permalink ---


def test_only_files_without_stored_permalinks_get_a_generated_permalink(monkeypatch) -> None:
    generated: list[str] = []
    original = utils.generate_permalink

    def counting_generate_permalink(file_path, split_extension=True):
        generated.append(str(file_path))
        return original(file_path, split_extension)

    monkeypatch.setattr(utils, "generate_permalink", counting_generate_permalink)
    existing = [stored(f"notes/note-{index}.md", f"notes/note-{index}") for index in range(2_000)]
    existing += [stored("notes/New!.md", "notes/new"), stored("files/a.png", None)]

    conflicts = detect_potential_file_conflicts("notes/new.md", existing)

    # One permalink for the checked file, one to confirm the single stored-permalink
    # candidate, and one per file without a stored permalink.
    assert conflicts == ["notes/New!.md"]
    assert generated == ["notes/new.md", "notes/New!.md", "files/a.png"]


def test_stored_permalink_check_is_faster_than_regenerating_every_permalink() -> None:
    paths = [f"notes/Topic {index}/Some Note Title {index}.md" for index in range(5_000)]
    existing = [stored(path, generate_permalink(path)) for path in paths]

    started = time.perf_counter()
    reference_detect_conflicts("notes/new.md", paths)
    regenerate_seconds = time.perf_counter() - started

    started = time.perf_counter()
    detect_potential_file_conflicts("notes/new.md", existing)
    stored_seconds = time.perf_counter() - started

    assert stored_seconds * 2 < regenerate_seconds
