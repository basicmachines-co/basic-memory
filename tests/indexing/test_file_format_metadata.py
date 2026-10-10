"""Non-Markdown file entities store their MIME type and short format at index time."""

from datetime import UTC, datetime

import pytest

import basic_memory.file_types as file_types
from basic_memory import db
from basic_memory.index.local_dependencies import build_local_markdown_file_indexer
from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.file_types import (
    FILE_FORMAT_BY_CONTENT_TYPE,
    file_entity_metadata,
    file_format,
)
from basic_memory.indexing.models import (
    IndexInputFile,
    StorageIndexFileWriter,
    index_file_job_result_from_indexed_file,
)
from basic_memory.models import Entity
from basic_memory.services.directory_service import DirectoryService

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# --- Format table ---


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("application/pdf", "pdf"),
        ("image/png", "png"),
        ("image/jpeg", "jpeg"),
        ("image/svg+xml", "svg"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
        ("text/csv", "csv"),
        ("text/plain; charset=utf-8", "txt"),
        ("Application/JSON", "json"),
        ("application/zip", "zip"),
        # Not in the table: a plain one-word subtype is the format.
        ("image/heic", "heic"),
        ("video/mp4", "mp4"),
        # Not in the table and not a plain word: no format is invented.
        ("application/octet-stream", None),
        ("application/vnd.example.thing", None),
        ("application/ld+json", None),
        ("text/x-python", None),
        ("not-a-mime-type", None),
    ],
)
def test_file_format_uses_the_table_then_a_simple_subtype(
    content_type: str, expected: str | None
) -> None:
    assert file_format(content_type) == expected


def test_format_table_values_are_short_lowercase_names() -> None:
    for content_type, name in FILE_FORMAT_BY_CONTENT_TYPE.items():
        assert content_type == content_type.lower()
        assert name.isalnum() and name == name.lower()


@pytest.mark.parametrize("content_type", ["application/pdf", "application/octet-stream"])
def test_file_metadata_never_uses_keys_that_note_readers_interpret(content_type: str) -> None:
    metadata = file_entity_metadata(content_type)

    assert metadata["content_type"] == content_type
    assert not set(metadata) & {
        "title",
        "type",
        "permalink",
        "tags",
        "schema",
        "entity",
        "embed",
    }


# --- Indexing and directory listings ---


@pytest.fixture
def batch_indexer(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
) -> BatchIndexer:
    return BatchIndexer(
        project_id=relation_repository.project_id,
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        observation_repository=entity_service.observation_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        file_writer=StorageIndexFileWriter(storage=file_service),
        session_maker=search_service.session_maker,
    )


def input_file(path: str, content_type: str, content: bytes) -> IndexInputFile:
    return IndexInputFile(path=path, content_type=content_type, content=content, size=len(content))


async def stored_metadata(entity_repository, session_maker, path: str) -> dict[str, object] | None:
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, path)
    assert entity is not None
    return entity.entity_metadata


async def test_new_files_store_their_format_and_updates_keep_it(
    batch_indexer: BatchIndexer, entity_repository, session_maker
) -> None:
    files = {
        "docs/report.pdf": input_file("docs/report.pdf", "application/pdf", b"%PDF-1"),
        "img/logo.png": input_file("img/logo.png", "image/png", b"\x89PNG"),
        "bin/blob.dat": input_file("bin/blob.dat", "application/octet-stream", b"\x00\x01"),
    }

    created = await batch_indexer.index_files(files, max_concurrent=1)

    assert created.errors == []
    assert await stored_metadata(entity_repository, session_maker, "docs/report.pdf") == {
        "content_type": "application/pdf",
        "format": "pdf",
    }
    assert await stored_metadata(entity_repository, session_maker, "img/logo.png") == {
        "content_type": "image/png",
        "format": "png",
    }
    assert await stored_metadata(entity_repository, session_maker, "bin/blob.dat") == {
        "content_type": "application/octet-stream",
    }

    updated = await batch_indexer.index_files(
        {"docs/report.pdf": input_file("docs/report.pdf", "application/pdf", b"%PDF-2")},
        max_concurrent=1,
    )

    assert updated.errors == []
    assert await stored_metadata(entity_repository, session_maker, "docs/report.pdf") == {
        "content_type": "application/pdf",
        "format": "pdf",
    }


async def test_directory_listings_expose_the_stored_format(
    batch_indexer: BatchIndexer,
    directory_service: DirectoryService,
    entity_repository,
    session_maker,
) -> None:
    await batch_indexer.index_files(
        {"docs/report.pdf": input_file("docs/report.pdf", "application/pdf", b"%PDF-1")},
        max_concurrent=1,
    )
    # A note's metadata is frontmatter; a "format" key there is not a file format.
    now = datetime.now(UTC)
    async with db.scoped_session(session_maker) as session:
        await entity_repository.add(
            session,
            Entity(
                title="Essay",
                note_type="note",
                content_type="text/markdown",
                file_path="docs/essay.md",
                checksum="essay",
                entity_metadata={"format": "long-form"},
                created_at=now,
                updated_at=now,
            ),
        )

    listing = await directory_service.list_directory("/docs")
    tree = await directory_service.get_directory_tree()

    formats = {node.name: node.format for node in listing.nodes}
    assert formats == {"report.pdf": "pdf", "essay.md": None}
    (docs,) = [node for node in tree.children if node.name == "docs"]
    assert {node.name: node.format for node in docs.children} == formats


# --- Index job results ---


async def test_index_job_results_carry_the_stored_content_type(
    monkeypatch: pytest.MonkeyPatch,
    batch_indexer: BatchIndexer,
    entity_repository,
    search_service,
    file_service,
    session_maker,
) -> None:
    # A slim container image has no MIME database; Office types must still resolve.
    monkeypatch.setattr(file_types.mimetypes, "guess_type", lambda name: (None, None))
    indexer = build_local_markdown_file_indexer(
        project_id=entity_repository.project_id,
        file_service=file_service,
        session_maker=session_maker,
        entity_repository=entity_repository,
        batch_indexer=batch_indexer,
        search_service=search_service,
    )
    (file_service.base_path / "docs").mkdir(parents=True, exist_ok=True)
    (file_service.base_path / "docs/plan.docx").write_bytes(b"PK\x03\x04docx")
    (file_service.base_path / "docs/note.md").write_text("# Note\n\nBody.\n", encoding="utf-8")

    docx = index_file_job_result_from_indexed_file(
        await indexer.index_file("docs/plan.docx", source="index")
    )
    note = index_file_job_result_from_indexed_file(
        await indexer.index_file("docs/note.md", source="index")
    )

    assert docx.content_type == DOCX
    assert note.content_type == "text/markdown"
    assert await stored_metadata(entity_repository, session_maker, "docs/plan.docx") == {
        "content_type": DOCX,
        "format": "docx",
    }
