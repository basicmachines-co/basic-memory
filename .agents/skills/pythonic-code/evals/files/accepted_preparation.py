"""Condensed accepted-note preparation composition based on Basic Memory issue #1098."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class NoteSchema:
    title: str
    file_path: str
    body: str


class PreparedEntityFieldsSource(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def file_path(self) -> str: ...

    @property
    def permalink(self) -> str: ...


class PreparedMarkdownSource(Protocol):
    @property
    def markdown(self) -> str: ...

    @property
    def search_text(self) -> str: ...

    @property
    def entity_fields(self) -> PreparedEntityFieldsSource: ...


@dataclass(frozen=True, slots=True)
class PreparedEntityFields:
    title: str
    file_path: str
    permalink: str


@dataclass(frozen=True, slots=True)
class PreparedMarkdown:
    markdown: str
    search_text: str
    entity_fields: PreparedEntityFields


class FileStore(Protocol):
    async def exists(self, file_path: str) -> bool: ...


class PermalinkResolver(Protocol):
    async def resolve(self, file_path: str, title: str) -> str: ...


class MarkdownParser(Protocol):
    async def parse(self, file_path: str, markdown: str) -> None: ...


class EntityService:
    """Full file/DB service; accepted composition currently builds it only to prepare."""

    def __init__(
        self,
        *,
        file_store: FileStore,
        permalink_resolver: PermalinkResolver,
        markdown_parser: MarkdownParser,
        entity_repository: object,
        observation_repository: object,
        relation_repository: object,
        search_service: object,
    ) -> None:
        self.file_store = file_store
        self.permalink_resolver = permalink_resolver
        self.markdown_parser = markdown_parser
        self.entity_repository = entity_repository
        self.observation_repository = observation_repository
        self.relation_repository = relation_repository
        self.search_service = search_service

    def _build_fields(
        self,
        schema: NoteSchema,
        permalink: str,
    ) -> PreparedEntityFields:
        return PreparedEntityFields(
            title=schema.title,
            file_path=schema.file_path,
            permalink=permalink,
        )

    async def _build_prepared(
        self,
        schema: NoteSchema,
        fields: PreparedEntityFields,
    ) -> PreparedMarkdown:
        markdown = f"---\npermalink: {fields.permalink}\n---\n# {schema.title}\n\n{schema.body}\n"
        await self.markdown_parser.parse(schema.file_path, markdown)
        return PreparedMarkdown(
            markdown=markdown,
            search_text=f"{schema.title}\n\n{schema.body}",
            entity_fields=fields,
        )

    async def prepare_create_entity_content(
        self,
        schema: NoteSchema,
    ) -> PreparedMarkdownSource:
        if await self.file_store.exists(schema.file_path):
            raise FileExistsError(schema.file_path)
        permalink = await self.permalink_resolver.resolve(schema.file_path, schema.title)
        fields = self._build_fields(schema, permalink)
        return await self._build_prepared(schema, fields)

    async def create_entity(self, schema: NoteSchema) -> PreparedMarkdownSource:
        prepared = await self.prepare_create_entity_content(schema)
        return prepared


@dataclass(frozen=True, slots=True)
class LocalAcceptedNotePreparerFactory:
    file_store: FileStore
    permalink_resolver: PermalinkResolver
    markdown_parser: MarkdownParser
    entity_repository: object
    observation_repository: object
    relation_repository: object
    search_service: object

    def create_note_preparer(self) -> EntityService:
        return EntityService(
            file_store=self.file_store,
            permalink_resolver=self.permalink_resolver,
            markdown_parser=self.markdown_parser,
            entity_repository=self.entity_repository,
            observation_repository=self.observation_repository,
            relation_repository=self.relation_repository,
            search_service=self.search_service,
        )


async def prepare_accepted_note_create(
    factory: LocalAcceptedNotePreparerFactory,
    schema: NoteSchema,
) -> PreparedMarkdownSource:
    preparer = factory.create_note_preparer()
    return await preparer.prepare_create_entity_content(schema)
