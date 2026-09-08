"""Local document run identities must resolve to the actual indexed sidecar."""

from pathlib import Path

import pytest
from httpx import AsyncClient

from basic_memory.document_ingestion.local_runtime import (
    ApiDocumentSourceEntityResolver,
    LocalDocumentSourceReader,
    LocalRawDocumentWriter,
    default_document_extractors,
)
from basic_memory.document_ingestion.raw_document import RawDocumentRuntime
from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.mcp.clients import KnowledgeClient, ProjectClient
from basic_memory.models import Project
from basic_memory.schemas.document import parse_document_ingestion_run_markdown
from basic_memory.services.file_service import FileService


@pytest.mark.asyncio
async def test_local_run_points_to_the_indexed_document(
    client: AsyncClient,
    test_project: Project,
) -> None:
    home = Path(test_project.path)
    (home / "riders.csv").write_bytes(b"team,name\nAST,Ana\n")
    await ProjectClient(client).index(
        test_project.external_id, force_full=False, run_in_background=False
    )
    knowledge = KnowledgeClient(client, test_project.external_id)
    files = FileService(home, MarkdownProcessor(EntityParser(home)))
    runtime = RawDocumentRuntime(
        source_resolver=ApiDocumentSourceEntityResolver(knowledge),
        source_reader=LocalDocumentSourceReader(home),
        extractors=default_document_extractors(),
        writer=LocalRawDocumentWriter(files, knowledge),
    )

    result = await runtime.ingest(file_path="riders.csv", observed_etag=None)
    indexed = await knowledge.get_entity(str(result.document_external_id))
    run = parse_document_ingestion_run_markdown(await files.read_file_content(result.run_file_path))

    assert indexed.file_path == result.document_file_path
    assert run.frontmatter.output is not None
    assert str(run.frontmatter.output.document_entity_external_id) == indexed.external_id
