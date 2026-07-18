import asyncio
from dataclasses import dataclass, field

import pytest

from accepted_preparation import (
    EntityService,
    LocalAcceptedNotePreparerFactory,
    NoteSchema,
    prepare_accepted_note_create,
)


@dataclass
class FakeFileStore:
    existing_paths: set[str] = field(default_factory=set)

    async def exists(self, file_path: str) -> bool:
        return file_path in self.existing_paths


@dataclass
class FakePermalinkResolver:
    async def resolve(self, file_path: str, title: str) -> str:
        return f"notes/{title.lower().replace(' ', '-')}"


@dataclass
class RecordingMarkdownParser:
    parsed: list[tuple[str, str]] = field(default_factory=list)

    async def parse(self, file_path: str, markdown: str) -> None:
        self.parsed.append((file_path, markdown))


def build_factory(file_store: FakeFileStore) -> LocalAcceptedNotePreparerFactory:
    return LocalAcceptedNotePreparerFactory(
        file_store=file_store,
        permalink_resolver=FakePermalinkResolver(),
        markdown_parser=RecordingMarkdownParser(),
        entity_repository=object(),
        observation_repository=object(),
        relation_repository=object(),
        search_service=object(),
    )


def note_schema() -> NoteSchema:
    return NoteSchema(title="Project Plan", file_path="plans/project-plan.md", body="Ship it.")


def test_accepted_create_prepares_the_canonical_markdown() -> None:
    prepared = asyncio.run(
        prepare_accepted_note_create(build_factory(FakeFileStore()), note_schema())
    )

    assert prepared.entity_fields.permalink == "notes/project-plan"
    assert prepared.search_text == "Project Plan\n\nShip it."
    assert prepared.markdown == (
        "---\npermalink: notes/project-plan\n---\n# Project Plan\n\nShip it.\n"
    )


def test_entity_service_uses_the_same_prepare_semantics() -> None:
    factory = build_factory(FakeFileStore())
    service = factory.create_note_preparer()
    assert isinstance(service, EntityService)

    prepared = asyncio.run(service.create_entity(note_schema()))

    assert prepared.entity_fields.file_path == "plans/project-plan.md"
    assert prepared.entity_fields.permalink == "notes/project-plan"


def test_existing_file_is_rejected_before_permalink_resolution() -> None:
    factory = build_factory(FakeFileStore(existing_paths={"plans/project-plan.md"}))

    with pytest.raises(FileExistsError, match="plans/project-plan.md"):
        asyncio.run(prepare_accepted_note_create(factory, note_schema()))
