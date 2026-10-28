"""Find/replace rejects projected output before allocating it (#1897)."""

import tracemalloc

import pytest

from basic_memory.services.note_preparation import apply_edit_operation
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.entity_service import EntityService
from basic_memory.services.file_service import FileService


@pytest.mark.parametrize(
    ("current", "replacement", "find", "count"),
    [
        ("a" * 4097, "b" * 4096, "a", 4097),
        ("a" * 2049, "é" * 4096, "a", 2049),
        ("é" * 4097, "🧠" * 2048, "é", 4097),
    ],
    ids=["ascii", "multibyte-output", "multibyte-find"],
)
def test_find_replace_rejects_oversize_before_allocation(
    current: str, replacement: str, find: str, count: int
):
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="find_replace.*16777216.*UTF-8"):
            apply_edit_operation(
                current, "find_replace", replacement, find_text=find, expected_replacements=count
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Input-sized accounting is allowed; constructing the amplified output is not.
    assert peak < 1024 * 1024


@pytest.mark.parametrize(
    "replacement", ["b" * 4096, "é" * 2048, "🧠" * 1024], ids=["ascii", "two-byte", "four-byte"]
)
def test_find_replace_accepts_exact_byte_limit(replacement: str):
    output = apply_edit_operation(
        "a" * 4096, "find_replace", replacement, find_text="a", expected_replacements=4096
    )
    assert len(output.encode("utf-8")) == 16 * 1024 * 1024
    assert output == replacement * 4096


def test_find_replace_can_shrink_an_oversize_existing_note():
    assert (
        apply_edit_operation(
            "é" * (8 * 1024 * 1024 + 1),
            "find_replace",
            "",
            find_text="é",
            expected_replacements=8 * 1024 * 1024 + 1,
        )
        == ""
    )


def test_find_replace_counts_nonoverlapping_matches():
    assert (
        apply_edit_operation("aaaaa", "find_replace", "é", find_text="aa", expected_replacements=2)
        == "ééa"
    )


@pytest.mark.asyncio
async def test_oversize_replacement_leaves_note_unchanged(
    entity_service: EntityService, file_service: FileService
):
    entity = await entity_service.create_entity(
        EntitySchema(title="Budget", directory="test", content="x" * 5000, note_type="note")
    )
    assert entity.permalink is not None
    file_path = file_service.get_entity_path(entity)
    before, _ = await file_service.read_file(file_path)
    checksum = entity.checksum

    with pytest.raises(ValueError, match="find_replace.*UTF-8"):
        await entity_service.edit_entity(
            entity.permalink,
            "find_replace",
            "y" * 4096,
            find_text="x",
            expected_replacements=5000,
        )

    after, _ = await file_service.read_file(file_path)
    assert after == before
    stored = await entity_service.link_resolver.resolve_link(entity.permalink, strict=True)
    assert stored is not None
    assert stored.checksum == checksum
