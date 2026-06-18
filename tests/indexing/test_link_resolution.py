"""Tests for portable batch link-text resolution."""

from basic_memory.indexing.link_resolution import LinkResolutionTarget, resolve_link_texts


def test_resolve_link_texts_matches_permalink_title_and_file_path() -> None:
    resolved = resolve_link_texts(
        [
            "Lookup Target",
            "lookup-target",
            "folder/lookup-target.md",
            "folder/lookup-target",
            "Missing Target",
        ],
        [
            LinkResolutionTarget(
                entity_id=42,
                permalink="lookup-target",
                title="Lookup Target",
                file_path="folder/lookup-target.md",
            )
        ],
    )

    assert resolved == {
        "Lookup Target": 42,
        "lookup-target": 42,
        "folder/lookup-target.md": 42,
        "folder/lookup-target": 42,
        "Missing Target": None,
    }


def test_resolve_link_texts_normalizes_wikilinks_and_aliases() -> None:
    resolved = resolve_link_texts(
        [
            "[[Lookup Target]]",
            "[[lookup-target|Read this]]",
            " lookup-target ",
        ],
        [
            LinkResolutionTarget(
                entity_id=42,
                permalink="lookup-target",
                title="Lookup Target",
                file_path="folder/lookup-target.md",
            )
        ],
    )

    assert resolved == {
        "[[Lookup Target]]": 42,
        "[[lookup-target|Read this]]": 42,
        " lookup-target ": 42,
    }


def test_resolve_link_texts_keeps_first_title_match() -> None:
    resolved = resolve_link_texts(
        ["Shared Title"],
        [
            LinkResolutionTarget(
                entity_id=1,
                permalink="first",
                title="Shared Title",
                file_path="first.md",
            ),
            LinkResolutionTarget(
                entity_id=2,
                permalink="second",
                title="Shared Title",
                file_path="second.md",
            ),
        ],
    )

    assert resolved == {"Shared Title": 1}


def test_resolve_link_texts_ignores_targets_without_ids() -> None:
    resolved = resolve_link_texts(
        ["No Id"],
        [
            LinkResolutionTarget(
                entity_id=None,
                permalink="no-id",
                title="No Id",
                file_path="no-id.md",
            )
        ],
    )

    assert resolved == {"No Id": None}
