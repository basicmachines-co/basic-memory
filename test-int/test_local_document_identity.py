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
from basic_memory.document_ingestion.raw_document import RawDocumentRuntime, canonical_db_checksum
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

    assert result.document_db_checksum == canonical_db_checksum(
        await files.compute_checksum(result.document_file_path)
    )
    assert indexed.file_path == result.document_file_path
    assert run.frontmatter.output is not None
    assert str(run.frontmatter.output.document_entity_external_id) == indexed.external_id
    reused = await runtime.ingest(file_path="riders.csv", observed_etag=None)
    assert reused.document_created is False
    assert reused.run_created is False
    (home / "riders.csv").write_bytes(b"team,name\nAST,Bea\n")
    refreshed = await runtime.ingest(file_path="riders.csv", observed_etag=None)
    assert refreshed.document_created is True
    assert refreshed.document_db_checksum == canonical_db_checksum(
        await files.compute_checksum(refreshed.document_file_path)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["move", "recreate"])
async def test_local_import_refuses_changed_source_ownership(
    client: AsyncClient,
    test_project: Project,
    change: str,
) -> None:
    from basic_memory.document_ingestion.local_runtime import DocumentSidecarConflictError

    home = Path(test_project.path)
    source_path = "riders.csv"
    (home / source_path).write_bytes(b"team,name\nAST,Ana\n")
    projects = ProjectClient(client)
    await projects.index(test_project.external_id, force_full=False, run_in_background=False)
    knowledge = KnowledgeClient(client, test_project.external_id)
    files = FileService(home, MarkdownProcessor(EntityParser(home)))
    runtime = RawDocumentRuntime(
        source_resolver=ApiDocumentSourceEntityResolver(knowledge),
        source_reader=LocalDocumentSourceReader(home),
        extractors=default_document_extractors(),
        writer=LocalRawDocumentWriter(files, knowledge),
    )
    first = await runtime.ingest(file_path=source_path, observed_etag=None)
    sidecar_before = (home / first.document_file_path).read_bytes()
    run_before = (home / first.run_file_path).read_bytes()
    source = await knowledge.resolve_entity_response(source_path, strict=True)

    if change == "move":
        source_path = "moved.csv"
        (home / "riders.csv").rename(home / source_path)
        await projects.index(test_project.external_id, force_full=False, run_in_background=False)
        moved = await knowledge.resolve_entity_response(source_path, strict=True)
        assert moved.external_id == source.external_id
    else:
        await knowledge.delete_entity(source.external_id)
        (home / source_path).write_bytes(b"team,name\nAST,Bea\n")
        await projects.index(test_project.external_id, force_full=False, run_in_background=False)

    with pytest.raises(DocumentSidecarConflictError):
        await runtime.ingest(file_path=source_path, observed_etag=None)

    assert (home / first.document_file_path).read_bytes() == sidecar_before
    assert (home / first.run_file_path).read_bytes() == run_before
    indexed = await knowledge.get_entity(str(first.document_external_id))
    assert indexed.file_path == first.document_file_path
    if change == "move":
        assert not (home / "moved.csv.md").exists()
