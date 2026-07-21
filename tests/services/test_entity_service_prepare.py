"""Parity tests for prepare-first entity write semantics."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from basic_memory.file_utils import ParseError, parse_frontmatter, remove_frontmatter
from basic_memory.repository import AcceptedObservationWrite, AcceptedRelationWrite
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.exceptions import EntityAlreadyExistsError
from basic_memory.services.entity_service import PreparedEntityFields
from basic_memory.services.note_preparation import _merge_metadata_into_markdown


@pytest.mark.asyncio
async def test_prepare_create_entity_content_matches_create_entity_with_content(
    entity_service,
) -> None:
    schema = EntitySchema(
        title="Prepared Create",
        directory="notes",
        note_type="note",
        content=(
            "---\n"
            "status: draft\n"
            "permalink: prepared/create\n"
            "created: 2024-01-15T10:30:00Z\n"
            "modified: 2024-01-16T11:45:00Z\n"
            "---\n"
            "Create body"
        ),
    )

    prepared = await entity_service.prepare_create_entity_content(schema)
    result = await entity_service.create_entity_with_content(schema)

    assert prepared.file_path.as_posix() == result.entity.file_path
    assert prepared.markdown_content == result.content
    assert prepared.search_content == result.search_content
    assert prepared.entity_fields.title == result.entity.title
    assert prepared.entity_fields.note_type == result.entity.note_type
    assert prepared.entity_fields.permalink == result.entity.permalink
    assert prepared.entity_fields.entity_metadata == result.entity.entity_metadata
    assert prepared.entity_fields.created_at == result.entity.created_at
    assert prepared.entity_fields.updated_at == result.entity.updated_at


@pytest.mark.asyncio
async def test_prepare_create_entity_content_returns_typed_entity_fields(entity_service) -> None:
    prepared = await entity_service.prepare_create_entity_content(
        EntitySchema(
            title="Typed Fields",
            directory="notes",
            note_type="decision",
            content=(
                "---\n"
                "status: accepted\n"
                "created: 2024-01-15T10:30:00Z\n"
                "modified: 2024-01-16T11:45:00+05:30\n"
                "---\n"
                "Body"
            ),
        )
    )

    assert prepared.entity_fields == PreparedEntityFields(
        title="Typed Fields",
        note_type="decision",
        entity_metadata={
            "title": "Typed Fields",
            "type": "decision",
            "status": "accepted",
            "created": "2024-01-15T10:30:00+00:00",
            "modified": "2024-01-16T11:45:00+05:30",
            "permalink": "test-project/notes/typed-fields",
        },
        content_type="text/markdown",
        permalink="test-project/notes/typed-fields",
        file_path="notes/Typed Fields.md",
        created_at=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
        updated_at=datetime.fromisoformat("2024-01-16T11:45:00+05:30"),
    )
    with pytest.raises(FrozenInstanceError):
        setattr(prepared.entity_fields, "title", "Changed")


@pytest.mark.asyncio
async def test_prepare_create_entity_content_exposes_parsed_graph(entity_service) -> None:
    """PreparedEntityWrite maps the parsed markdown graph for the accepted-write path.

    The DB-first accepted-write path reuses this parsed graph instead of
    reparsing the materialized file, so a mapping regression here would leave
    the observation/relation tables empty after a successful write (issue #1076).
    """
    prepared = await entity_service.prepare_create_entity_content(
        EntitySchema(
            title="Ada Acceptance",
            directory="notes",
            note_type="dev_accept_person",
            content=(
                "## Observations\n"
                "- [name] Ada Acceptance #person\n"
                "- [role] Engineer (staff)\n"
                "\n"
                "## Relations\n"
                "- works_at [[XSYS Target]]\n"
            ),
        )
    )

    assert prepared.observations == [
        AcceptedObservationWrite(
            # The parser keeps the inline #tag in the content and also extracts it,
            # matching how the file-index path stores observation rows.
            content="Ada Acceptance #person",
            category="name",
            context=None,
            tags=["person"],
        ),
        AcceptedObservationWrite(
            content="Engineer",
            category="role",
            context="staff",
            tags=None,
        ),
    ]
    assert prepared.relations == [
        AcceptedRelationWrite(
            relation_type="works_at",
            target_name="XSYS Target",
            context=None,
        )
    ]


@pytest.mark.asyncio
async def test_prepare_create_entity_content_can_skip_storage_existence_check(
    entity_service,
) -> None:
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("file_service.exists should not be called")

    entity_service.file_service.exists = fail_if_called

    prepared = await entity_service.prepare_create_entity_content(
        EntitySchema(
            title="Prepared Create No HEAD",
            directory="notes",
            note_type="note",
            content="Create body",
        ),
        check_storage_exists=False,
    )

    assert prepared.file_path.as_posix() == "notes/Prepared Create No HEAD.md"
    assert prepared.entity_fields.title == "Prepared Create No HEAD"


@pytest.mark.asyncio
async def test_prepare_update_entity_content_matches_update_entity_with_content(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Update",
            directory="notes",
            note_type="note",
            content="---\nstatus: draft\nowner: alice\n---\nOriginal body",
        )
    )

    existing_content = await file_service.read_file_content(created.file_path)
    update_schema = EntitySchema(
        title="Prepared Update",
        directory="notes",
        note_type="note",
        content="---\nstatus: published\nreviewed_by: bob\n---\nUpdated body",
    )

    prepared = await entity_service.prepare_update_entity_content(
        created,
        update_schema,
        existing_content,
    )
    original_created_at = created.created_at
    result = await entity_service.update_entity_with_content(created, update_schema)
    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)

    assert prepared.markdown_content == result.content
    assert prepared.search_content == result.search_content
    assert prepared.entity_fields.title == result.entity.title
    assert prepared.entity_fields.note_type == result.entity.note_type
    assert prepared.entity_fields.permalink == result.entity.permalink
    assert prepared.entity_fields.created_at == original_created_at
    assert result.entity.created_at == original_created_at
    assert prepared_frontmatter["owner"] == "alice"
    assert prepared_frontmatter["status"] == "published"
    assert prepared_frontmatter["reviewed_by"] == "bob"


@pytest.mark.asyncio
async def test_prepare_update_entity_content_can_change_file_path(
    entity_service,
    file_service,
) -> None:
    """Full replacements should carry title/directory renames through prepare state."""
    created = await entity_service.create_entity(
        EntitySchema(
            title="Original Name",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )

    existing_content = await file_service.read_file_content(created.file_path)
    update_schema = EntitySchema(
        title="Renamed Note",
        directory="journal",
        note_type="note",
        content="Renamed body",
    )

    prepared = await entity_service.prepare_update_entity_content(
        created,
        update_schema,
        existing_content,
    )
    result = await entity_service.update_entity_with_content(created, update_schema)

    assert prepared.file_path.as_posix() == "journal/Renamed Note.md"
    assert result.entity.file_path == "journal/Renamed Note.md"
    assert result.content == prepared.markdown_content
    assert prepared.entity_fields.permalink != created.permalink
    assert prepared.entity_fields.permalink == result.entity.permalink
    assert not await file_service.exists("notes/Original Name.md")
    assert await file_service.exists("journal/Renamed Note.md")


@pytest.mark.asyncio
async def test_prepare_update_entity_content_can_repair_invalid_canonical_timestamps(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Repair Timestamps",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )
    invalid_content = (
        "---\ncreated: yesterday\nmodified: last week\nowner: alice\n---\nOriginal body"
    )
    await file_service.write_file(created.file_path, invalid_content)
    update_schema = EntitySchema(
        title="Repair Timestamps",
        directory="notes",
        note_type="note",
        content=(
            "---\ncreated: 2024-01-15T10:30:00Z\nmodified: 2024-01-16T11:45:00Z\n---\nRepaired body"
        ),
    )

    prepared = await entity_service.prepare_update_entity_content(
        created,
        update_schema,
        invalid_content,
    )
    result = await entity_service.update_entity_with_content(created, update_schema)

    assert prepared.entity_fields.created_at == datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    assert prepared.entity_fields.updated_at == datetime(2024, 1, 16, 11, 45, tzinfo=UTC)
    assert result.entity.created_at == prepared.entity_fields.created_at
    assert result.entity.updated_at == prepared.entity_fields.updated_at
    assert parse_frontmatter(result.content)["owner"] == "alice"


@pytest.mark.asyncio
async def test_prepare_update_entity_content_can_repair_malformed_frontmatter(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Repair Malformed Frontmatter",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )
    malformed_content = "---\nstatus: [draft\n---\nOriginal body"
    await file_service.write_file(created.file_path, malformed_content)
    update_schema = EntitySchema(
        title="Repair Malformed Frontmatter",
        directory="notes",
        note_type="note",
        content=(
            "---\ncreated: 2024-01-15T10:30:00Z\nmodified: 2024-01-16T11:45:00Z\n"
            "status: repaired\n---\nRepaired body"
        ),
    )

    prepared = await entity_service.prepare_update_entity_content(
        created,
        update_schema,
        malformed_content,
    )
    result = await entity_service.update_entity_with_content(created, update_schema)

    assert prepared.entity_fields.created_at == datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    assert prepared.entity_fields.updated_at == datetime(2024, 1, 16, 11, 45, tzinfo=UTC)
    assert result.content == prepared.markdown_content
    assert parse_frontmatter(result.content)["status"] == "repaired"
    assert remove_frontmatter(result.content) == "Repaired body"


@pytest.mark.asyncio
async def test_prepare_update_entity_content_preserves_permalink_when_move_updates_disabled(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Stable Permalink",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )
    entity_service.app_config.update_permalinks_on_move = False

    existing_content = await file_service.read_file_content(created.file_path)
    update_schema = EntitySchema(
        title="Renamed Stable Permalink",
        directory="journal",
        note_type="note",
        content="Renamed body",
    )

    prepared = await entity_service.prepare_update_entity_content(
        created,
        update_schema,
        existing_content,
    )
    result = await entity_service.update_entity_with_content(created, update_schema)
    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)

    assert prepared.file_path.as_posix() == "journal/Renamed Stable Permalink.md"
    assert result.entity.file_path == "journal/Renamed Stable Permalink.md"
    assert prepared.entity_fields.permalink == created.permalink
    assert result.entity.permalink == created.permalink
    assert prepared_frontmatter["permalink"] == created.permalink
    assert not await file_service.exists("notes/Stable Permalink.md")
    assert await file_service.exists("journal/Renamed Stable Permalink.md")


@pytest.mark.asyncio
async def test_prepare_move_entity_content_updates_permalink_when_move_policy_enabled(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Move",
            directory="notes",
            note_type="note",
            content="Move body",
        )
    )
    entity_service.app_config.update_permalinks_on_move = True

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_move_entity_content(
        created,
        current_content,
        "archive/Prepared Move.md",
    )
    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)

    assert prepared.file_path.as_posix() == "archive/Prepared Move.md"
    assert prepared.permalink == "test-project/archive/prepared-move"
    assert prepared_frontmatter["permalink"] == "test-project/archive/prepared-move"
    assert prepared.search_content == remove_frontmatter(prepared.markdown_content)


@pytest.mark.asyncio
async def test_update_entity_with_content_rejects_rename_conflicts_before_writing(
    entity_service,
    file_service,
) -> None:
    source = await entity_service.create_entity(
        EntitySchema(
            title="Source Note",
            directory="notes",
            note_type="note",
            content="Source body",
        )
    )
    target = await entity_service.create_entity(
        EntitySchema(
            title="Target Note",
            directory="notes",
            note_type="note",
            content="Target body",
        )
    )

    source_content = await file_service.read_file_content(source.file_path)
    target_content = await file_service.read_file_content(target.file_path)

    with pytest.raises(
        EntityAlreadyExistsError,
        match="file already exists at destination path: notes/Target Note.md",
    ):
        await entity_service.update_entity_with_content(
            source,
            EntitySchema(
                title="Target Note",
                directory="notes",
                note_type="note",
                content="Overwritten body",
            ),
        )

    assert await file_service.read_file_content(source.file_path) == source_content
    assert await file_service.read_file_content(target.file_path) == target_content
    assert await file_service.exists(source.file_path)
    assert await file_service.exists(target.file_path)


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_matches_edit_entity_with_content(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Edit",
            directory="notes",
            note_type="note",
            content="Before edit",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="find_replace",
        content="After edit",
        find_text="Before edit",
    )
    original_created_at = created.created_at
    result = await entity_service.edit_entity_with_content(
        identifier=created.permalink,
        operation="find_replace",
        content="After edit",
        find_text="Before edit",
    )

    assert prepared.markdown_content == result.content
    assert prepared.search_content == result.search_content
    assert prepared.entity_fields.title == result.entity.title
    assert prepared.entity_fields.note_type == result.entity.note_type
    assert prepared.entity_fields.permalink == result.entity.permalink
    assert prepared.entity_fields.created_at == original_created_at
    assert result.entity.created_at == original_created_at


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_updates_title_when_h1_changes(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Original Title",
            directory="notes",
            note_type="note",
            content="# Original Title\n\nExisting body",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="find_replace",
        content="# Updated Title",
        find_text="# Original Title",
    )
    metadata = prepared.entity_fields.entity_metadata

    assert prepared.entity_fields.title == "Updated Title"
    assert parse_frontmatter(prepared.markdown_content)["title"] == "Updated Title"
    assert metadata is not None
    assert metadata["title"] == "Updated Title"
    assert "# Updated Title" in remove_frontmatter(prepared.markdown_content)


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_prepend_preserves_valid_frontmatter(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Prepend Frontmatter",
            directory="notes",
            note_type="note",
            content="---\nstatus: draft\ntags:\n  - one\n---\nOriginal body",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="prepend",
        content="Prepended line",
    )

    assert parse_frontmatter(prepared.markdown_content) == {
        "title": "Prepared Prepend Frontmatter",
        "type": "note",
        "status": "draft",
        "tags": ["one"],
        "permalink": created.permalink,
    }
    assert remove_frontmatter(prepared.markdown_content) == "Prepended line\nOriginal body"


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_prepend_fails_for_malformed_frontmatter(
    entity_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Prepend Parse Error",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )

    malformed_content = "---\nstatus: [draft\n---\nOriginal body"

    with pytest.raises(ParseError, match="Invalid YAML in frontmatter"):
        await entity_service.prepare_edit_entity_content(
            created,
            malformed_content,
            operation="prepend",
            content="Prepended line",
        )


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_prepend_without_frontmatter_uses_simple_prepend(
    entity_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Prepend Simple",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )

    prepared = await entity_service.prepare_edit_entity_content(
        created,
        "Original body",
        operation="prepend",
        content="Prepended line",
    )

    assert prepared.markdown_content == "Prepended line\nOriginal body"


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_metadata_adds_new_key(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Metadata New Key",
            directory="notes",
            note_type="note",
            content="---\nstatus: draft\n---\nBody",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="append",
        content="",
        metadata={"closed_at": "2026-06-18T10:42:00Z"},
    )

    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)
    assert prepared_frontmatter["closed_at"] == "2026-06-18T10:42:00Z"
    assert prepared_frontmatter["status"] == "draft"


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_metadata_overwrites_existing_key(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Metadata Overwrite",
            directory="notes",
            note_type="note",
            content="---\nstatus: draft\n---\nBody",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="append",
        content="",
        metadata={"status": "resolved"},
    )

    assert parse_frontmatter(prepared.markdown_content)["status"] == "resolved"


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_metadata_preserves_unrelated_keys_and_body(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Metadata Preserve",
            directory="notes",
            note_type="note",
            content="---\nstatus: draft\nowner: alice\n---\nOriginal body",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="append",
        content="\nMore body",
        metadata={"status": "resolved"},
    )

    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)
    assert prepared_frontmatter["owner"] == "alice"
    assert prepared_frontmatter["status"] == "resolved"
    assert "Original body" in remove_frontmatter(prepared.markdown_content)
    assert "More body" in remove_frontmatter(prepared.markdown_content)


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_metadata_ignores_identity_fields(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Metadata Identity Guard",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="append",
        content="",
        metadata={
            "title": "Hijacked Title",
            "type": "hijacked",
            "permalink": "hijacked/permalink",
            "status": "resolved",
        },
    )

    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)
    assert prepared_frontmatter["status"] == "resolved"
    assert prepared.entity_fields.title == "Metadata Identity Guard"
    assert prepared.entity_fields.permalink == created.permalink
    assert prepared_frontmatter["title"] == "Metadata Identity Guard"
    assert prepared_frontmatter["permalink"] == created.permalink


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_metadata_without_existing_frontmatter(
    entity_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Metadata No Frontmatter",
            directory="notes",
            note_type="note",
            content="Plain body",
        )
    )

    prepared = await entity_service.prepare_edit_entity_content(
        created,
        "Plain body",
        operation="append",
        content="",
        metadata={"status": "resolved"},
    )

    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)
    assert prepared_frontmatter["status"] == "resolved"
    assert "Plain body" in remove_frontmatter(prepared.markdown_content)


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_metadata_only_edit_preserves_body_exactly(
    entity_service,
) -> None:
    """A frontmatter-only request must not reflow the body (PR #1090 review).

    Trailing hard-break spaces, extra blank lines, and a missing final newline
    are all meaningful markdown; the merge must round-trip them byte-exact.
    """
    created = await entity_service.create_entity(
        EntitySchema(
            title="Metadata Body Fidelity",
            directory="notes",
            note_type="note",
            content="Plain body",
        )
    )
    body = "Line with hard break  \n\n\n  indented tail without trailing newline"
    current_content = f"---\nstatus: draft\n---\n\n{body}"

    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="append",
        content="",
        metadata={"status": "resolved"},
    )

    assert parse_frontmatter(prepared.markdown_content)["status"] == "resolved"
    assert prepared.markdown_content.endswith(f"---\n\n{body}")


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_metadata_only_edit_skips_append_newline(
    entity_service,
) -> None:
    """The documented metadata-only pattern must be a true body no-op (PR #1090 review).

    An empty append normally appends "\\n" to content without a trailing
    newline; combined with metadata that mutated a body the caller asked to
    leave untouched.
    """
    created = await entity_service.create_entity(
        EntitySchema(
            title="Metadata NoOp Append",
            directory="notes",
            note_type="note",
            content="Plain body",
        )
    )

    prepared = await entity_service.prepare_edit_entity_content(
        created,
        "Plain body without trailing newline",
        operation="append",
        content="",
        metadata={"status": "resolved"},
    )

    assert prepared.markdown_content.endswith("Plain body without trailing newline")


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_metadata_rejects_null_values(
    entity_service,
) -> None:
    """Null metadata values are rejected instead of silently dropping the field (PR #1090 review)."""
    created = await entity_service.create_entity(
        EntitySchema(
            title="Metadata Null Guard",
            directory="notes",
            note_type="note",
            content="---\nstatus: draft\n---\nBody",
        )
    )

    with pytest.raises(ValueError, match="key deletion is not supported.*status"):
        await entity_service.prepare_edit_entity_content(
            created,
            "---\nstatus: draft\n---\nBody",
            operation="append",
            content="",
            metadata={"status": None},
        )


def test_merge_metadata_into_markdown_identity_only_metadata_is_noop():
    """A merge holding only identity fields must leave the markdown byte-identical."""
    markdown = "---\nstatus: draft\n---\n\nBody  \n"
    merged = _merge_metadata_into_markdown(markdown, {"title": "X", "type": "y", "permalink": "z"})
    assert merged == markdown


def test_merge_metadata_into_markdown_preserves_crlf_body():
    """CRLF notes keep their body when the separator line is dropped for re-dumping."""
    markdown = "---\r\nstatus: draft\r\n---\r\n\r\nBody line\r\n"
    merged = _merge_metadata_into_markdown(markdown, {"status": "resolved"})
    assert merged.endswith("Body line\r\n")
    assert parse_frontmatter(merged)["status"] == "resolved"


def test_merge_metadata_into_markdown_preserves_separatorless_body():
    """A note whose body starts right after the closing fence must not gain a blank line."""
    markdown = "---\nstatus: draft\n---\nBody line\n"
    merged = _merge_metadata_into_markdown(markdown, {"status": "resolved"})
    assert merged.endswith("---\nBody line\n")
    assert "\n\nBody line" not in merged
    assert parse_frontmatter(merged)["status"] == "resolved"
