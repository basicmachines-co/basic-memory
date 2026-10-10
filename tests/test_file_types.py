"""Host-independent content types and their agreement with the format table."""

from pathlib import Path

import pytest

import basic_memory.file_types as file_types
from basic_memory.file_types import (
    CONTENT_TYPE_BY_EXTENSION,
    FILE_FORMAT_BY_CONTENT_TYPE,
    file_content_type,
    file_format,
    guess_content_type,
)
from basic_memory.services.file_service import FileService

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.fixture
def host_without_mime_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a slim image whose MIME database knows no extension at all."""

    def know_nothing(name: str) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(file_types.mimetypes, "guess_type", know_nothing)


@pytest.mark.usefixtures("host_without_mime_database")
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("plan.docx", DOCX),
        ("budget.xlsx", XLSX),
        ("deck.pptx", PPTX),
        ("REPORT.PDF", "application/pdf"),
        ("note.md", "text/markdown"),
        ("board.canvas", "application/json"),
        ("photo.webp", "image/webp"),
        ("data.csv", "text/csv"),
    ],
)
def test_supported_types_do_not_depend_on_the_host(name: str, expected: str) -> None:
    assert file_content_type(name) == expected


@pytest.mark.usefixtures("host_without_mime_database")
def test_unknown_extensions_fall_back_to_text_plain() -> None:
    assert guess_content_type("archive.unknownext") is None
    assert file_content_type("archive.unknownext") == "text/plain"


def test_other_extensions_use_the_host_database() -> None:
    assert guess_content_type("image.bmp") == "image/bmp"


def test_every_pinned_content_type_has_a_format() -> None:
    for extension, content_type in CONTENT_TYPE_BY_EXTENSION.items():
        assert extension == extension.lower() and extension.startswith(".")
        assert file_format(content_type) is not None, content_type
    assert set(CONTENT_TYPE_BY_EXTENSION.values()) <= set(FILE_FORMAT_BY_CONTENT_TYPE)


@pytest.mark.usefixtures("host_without_mime_database")
def test_file_service_uses_the_host_independent_table(file_service: FileService) -> None:
    assert file_service.content_type("docs/plan.docx") == DOCX
    assert file_service.content_type(Path("docs/deck.pptx")) == PPTX
    assert file_service.content_type("notes/board.canvas") == "application/json"
    assert file_service.content_type("notes/unknown.thing") == "text/plain"
