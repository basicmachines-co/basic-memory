"""Real OKF producer/consumer boundary, including pinned upstream compatibility."""

import importlib.util
import json
import os
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path
import shutil
import sys
from datetime import UTC, datetime
from types import ModuleType

import pytest

from basic_memory import db
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)
from basic_memory.okf.validation import check_bundle, check_document, parse_document
from basic_memory.okf.render import ExportFile, ExportSnapshot, render_bundle
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.schemas.search import SearchQuery

FIXTURES = Path(__file__).parents[1] / "tests/fixtures/okf"


def upstream_parser() -> ModuleType:
    # Load the unmodified pinned parser only in tests. Explicit counts ensure a
    # permissive consumer cannot hide malformed concepts by silently skipping them.
    spec = importlib.util.spec_from_loader(
        "okf_upstream_document",
        SourceFileLoader("okf_upstream_document", str(FIXTURES / "upstream_document.py.txt")),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def upstream_concept_count(root: Path) -> int:
    module = upstream_parser()
    count = 0
    for path in root.rglob("*.md"):
        if path.name in {"index.md", "log.md"}:
            continue
        document = module.OKFDocument.parse(path.read_text(encoding="utf-8"))
        document.validate()
        count += 1
    return count


@pytest.mark.asyncio
async def test_export_real_project(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "projects": {"export": {"path": str(root)}},
                "default_project": "export",
                "semantic_search_enabled": False,
                "index_changes": False,
                "env": "dev",
            }
        )
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("BASIC_MEMORY_")}
    env.update(
        BASIC_MEMORY_CONFIG_DIR=str(config_dir),
        BASIC_MEMORY_NO_PROMOS="1",
        BASIC_MEMORY_CLI_AUTO_UPDATE="false",
        HOME=str(tmp_path),
    )

    def cli(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "basic_memory.cli.main", *arguments],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    (root / "notes").mkdir()
    (root / "notes/source.md").write_text(
        "---\ntitle: Source\npermalink: source\ntype: note\ntags: [interop]\n"
        "sources: [{resource: /references/paper.pdf}]\n---\n# Source\n"
        "- [fact] Knowledge stays portable (evidence)\n"
        "- depends_on [[Target]] (design)\n"
        "See [[Target|the target]] and [paper](../references/paper.pdf).\n",
        encoding="utf-8",
    )
    cli(
        "tool",
        "write-note",
        "--title",
        "Target",
        "--folder",
        ".",
        "--content",
        "# Target\n",
        "--project",
        "export",
        "--local",
    )
    (root / "references").mkdir()
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
    (root / "references/paper.pdf").write_bytes(pdf)
    (root / "index.md").write_text(
        "---\nbm: {profile: wiki/1}\n---\n[[Live Wiki]]\n", encoding="utf-8"
    )
    source_paths = ["notes/source.md", "Target.md", "references/paper.pdf", "index.md"]
    before = {path: (root / path).read_bytes() for path in source_paths}
    destination = tmp_path / "bundle"
    report = json.loads(cli("okf", "export", str(destination), "--project", "export", "--json"))
    assert report["diagnostics"] == []
    assert report["concepts"] == upstream_concept_count(destination) == 2
    assert (destination / "references/paper.pdf").read_bytes() == pdf
    source = parse_document((destination / "notes/source.md").read_text())
    assert "[the target](/Target.md)" in source.body
    assert "- depends_on [Target](/Target.md) (design)" in source.body
    assert "- [fact] Knowledge stays portable (evidence)" in source.body
    assert "bm" in source.metadata
    log = (destination / "log.md").read_text()
    assert not log.startswith("---")
    assert "\n## " in log and "Target.md" in log
    assert parse_document((destination / "index.md").read_text()).metadata == {"okf_version": "0.2"}
    assert not parse_document((destination / "notes/index.md").read_text()).has_frontmatter
    assert before == {path: (root / path).read_bytes() for path in source_paths}
    first = {
        p.relative_to(destination): p.read_bytes() for p in destination.rglob("*") if p.is_file()
    }
    cli("okf", "export", str(destination), "--project", "export", "--replace", "--json")
    assert first == {
        p.relative_to(destination): p.read_bytes() for p in destination.rglob("*") if p.is_file()
    }


@pytest.mark.asyncio
async def test_upstream_sample_is_indexed_and_retrievable(
    test_project, engine_factory, search_service
):
    upstream = FIXTURES / "crypto_bitcoin"
    report = check_bundle(upstream)
    assert report.success, report.diagnostics
    assert report.concepts == upstream_concept_count(upstream) == 9
    root = Path(test_project.path)
    shutil.copytree(upstream, root, dirs_exist_ok=True)
    result = await run_local_project_index_for_project(
        test_project, runtime_factory=LocalProjectIndexRuntimeFactory(), force_full=True
    )
    assert sum(batch.failed_files for batch in result.batch_results) == 0
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        entities = await EntityRepository(project_id=test_project.id).find_all(session)
        concepts = [
            entity
            for entity in entities
            if Path(entity.file_path).name not in {"index.md", "log.md"}
        ]
        assert len(concepts) == 9
        edges = await RelationRepository(project_id=test_project.id).find_by_type(
            session, "links_to"
        )
        assert any(edge.to_id is not None and "transactions" in edge.to_name for edge in edges)
    results = await search_service.search(SearchQuery(text="Bitcoin"))
    assert results
    assert any("transactions" in result.file_path for result in results)
    target = root / "tables/transactions.md"
    assert "transaction" in target.read_text(encoding="utf-8").lower()


def test_upstream_sample_log_is_not_conformance_authority():
    text = (FIXTURES / "upstream_invalid_log.md").read_text()
    assert {diagnostic.rule for diagnostic in check_document("log.md", text)} == {
        "reserved.frontmatter"
    }


def test_upstream_timestamp_behavior_survives_export():
    module = upstream_parser()
    source = "--- \t\ntype: note\nstale_after: 2026-06-30T14:00:00Z\n---\t\n# A\n"
    exported = next(
        file.content.decode()
        for file in render_bundle(ExportSnapshot("p", (ExportFile("a.md", source.encode()),)))
        if file.path == "a.md"
    )
    for content in (source, exported):
        document = module.OKFDocument.parse(content)
        document.validate()
        assert module.is_stale(document.frontmatter, datetime(2026, 7, 1, tzinfo=UTC))
        assert not module.is_stale(document.frontmatter, datetime(2026, 6, 1, tzinfo=UTC))
