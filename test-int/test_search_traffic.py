"""How much a search reads from the database (#1746).

Basic Memory Cloud pays for, and bills, every byte Postgres sends to the application,
and search was most of that traffic: production searches moved megabytes because they
carried whole notes through ranking. These tests run real searches through the HTTP
route over notes the size of large production notes and count what came back.

The budget test runs in the ordinary suite. The benchmark seeds a larger project and
prints per-mode totals and the statements that returned the most bytes; set
``BASIC_MEMORY_SEARCH_TRAFFIC_NOTES`` to change its size.
"""

import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from basic_memory import db
from basic_memory.mcp.clients.search import SearchClient
from basic_memory.models.project import Project
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode
from basic_memory.services.search_service import SearchService
from statement_traffic import TrafficLog, record_traffic

# --- Fixture notes ---

# Production's large project averaged 41,522 characters of note text (#1746).
NOTE_CHARS = 40_000
OBSERVATIONS_PER_NOTE = 20
PAGE_SIZE = 10
VOCABULARY = (
    "search index vector chunk embedding ranking fusion query note project memory graph "
    "relation observation schema migration postgres sqlite token session context agent "
    "retrieval latency transfer bytes budget excerpt snippet window filter predicate"
).split()
QUERIES = (
    "vector chunk ranking",
    "how does the agent keep session context",
    "postgres migration budget",
)


def note_content(index: int) -> str:
    """A note of about NOTE_CHARS characters with observations and a relation."""
    words = random.Random(index)
    observations = "\n".join(
        f"- [fact] {' '.join(words.choices(VOCABULARY, k=12))}"
        for _ in range(OBSERVATIONS_PER_NOTE)
    )
    sections: list[str] = []
    length = 0
    while length < NOTE_CHARS:
        paragraph = " ".join(words.choices(VOCABULARY, k=120)) + "."
        sections.append(f"## Section {len(sections)}\n\n{paragraph}")
        length += len(paragraph)
    body = "\n\n".join(sections)
    return (
        f"# Traffic Note {index}\n\n{body}\n\n## Observations\n{observations}\n\n"
        f"## Relations\n- relates_to [[Traffic Note {(index + 1) % 1000}]]\n"
    )


async def seed_notes(search_service: SearchService, count: int) -> None:
    """Index ``count`` large notes, with vectors, the way a sync would."""
    for index in range(count):
        permalink = f"traffic/note-{index:05d}"
        async with db.scoped_session(search_service.session_maker) as session:
            entity = await search_service.entity_repository.create(
                session,
                {
                    "title": f"Traffic Note {index}",
                    "note_type": "note",
                    "entity_metadata": {"tags": ["traffic"], "status": "active"},
                    "content_type": "text/markdown",
                    "permalink": permalink,
                    "file_path": f"{permalink}.md",
                },
            )
        await search_service.index_entity_data(entity, content=note_content(index))
        await search_service.sync_entity_vectors(entity.id)


# --- Measuring a search ---


@dataclass(frozen=True, slots=True)
class SearchTraffic:
    case: str
    results: int
    elapsed_ms: float
    log: TrafficLog


async def measure_search(
    engine: AsyncEngine,
    client: AsyncClient,
    project: Project,
    mode: SearchRetrievalMode,
    text: str,
    *,
    filtered: bool = True,
) -> SearchTraffic:
    """Run one search through the HTTP route and record every statement it ran."""
    # The MCP tool sends entity_types=["entity"] when the caller names no types, which
    # makes every default search a filtered one; ``filtered=False`` is a direct API
    # call with no filters. min_similarity=0 keeps every vector match: the test
    # embedder is a hash, and a threshold would hide the vector leg.
    query = SearchQuery(
        text=text,
        retrieval_mode=mode,
        entity_types=[SearchItemType.ENTITY] if filtered else None,
        min_similarity=0.0,
    )
    started = time.perf_counter()
    with record_traffic(engine) as log:
        response = await SearchClient(client, project.external_id).search(
            query.model_dump(mode="json", exclude_none=True), page=1, page_size=PAGE_SIZE
        )
    return SearchTraffic(
        case=mode.value if filtered else f"{mode.value}-unfiltered",
        results=len(response.results),
        elapsed_ms=(time.perf_counter() - started) * 1000,
        log=log,
    )


def report(traffic: list[SearchTraffic], note_count: int, backend: str) -> None:
    """Print totals per mode and the statements that moved the most bytes."""
    print(f"\n=== search traffic: {backend}, {note_count} notes x {NOTE_CHARS} chars ===")
    for case in dict.fromkeys(run.case for run in traffic):
        runs = [run for run in traffic if run.case == case]
        mean_bytes = sum(run.log.bytes for run in runs) / len(runs)
        mean_rows = sum(run.log.rows for run in runs) / len(runs)
        mean_statements = sum(len(run.log.statements) for run in runs) / len(runs)
        mean_ms = sum(run.elapsed_ms for run in runs) / len(runs)
        print(
            f"{case:>17}: {mean_bytes / 1024:8.1f} KiB  {mean_rows:6.0f} rows  "
            f"{mean_statements:5.1f} statements  {mean_ms:7.1f} ms per search"
        )
        for label, size in runs[-1].log.largest(5):
            print(f"         {size / 1024:10.1f} KiB  {label}")

    output = os.environ.get("BASIC_MEMORY_BENCHMARK_OUTPUT")
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for run in traffic:
                record = {
                    "benchmark": "search_traffic",
                    "backend": backend,
                    "notes": note_count,
                    "case": run.case,
                    "elapsed_ms": run.elapsed_ms,
                    "bytes": run.log.bytes,
                    "rows": run.log.rows,
                    "statements": len(run.log.statements),
                }
                handle.write(json.dumps(record) + "\n")


# --- Tests ---


@pytest.mark.asyncio
# Each note is cut into over a hundred chunks; Postgres takes seconds to seed one.
@pytest.mark.timeout(600)
async def test_search_reads_bounded_text_per_page(
    engine_factory, client: AsyncClient, test_project: Project, search_service, app_config
):
    """A page of results reads about a page of text, not the notes it ranked."""
    # Two more notes than a page, so every page is full.
    note_count = PAGE_SIZE + 2
    await seed_notes(search_service, note_count)
    engine, _ = engine_factory

    traffic = [
        await measure_search(engine, client, test_project, mode, QUERIES[0])
        for mode in SearchRetrievalMode
    ]
    report(traffic, note_count, app_config.database_backend.value)

    corpus_bytes = note_count * NOTE_CHARS
    for run in traffic:
        assert run.results == PAGE_SIZE
        # Every note in the project matches; a search that ranked by note text would
        # read most of the corpus. A page of capped snippets and excerpts is far less.
        assert run.log.bytes < corpus_bytes / 4, (run.case, run.log.largest())
        # Notes here hold dozens of chunks each. The vector index ranks the window's
        # rows once and the page's excerpts once; a widening window would read again.
        # Nearest-neighbour queries: sqlite-vec's MATCH or pgvector's <=> operator.
        vector_reads = [
            statement
            for statement in run.log.statements
            if "embedding MATCH" in statement.sql or "<=>" in statement.sql
        ]
        assert len(vector_reads) <= 2, (run.case, [s.label for s in vector_reads])


@pytest.mark.asyncio
@pytest.mark.benchmark
# Seeding a few hundred 40 KB notes with vectors outlasts the suite's 120 s default.
@pytest.mark.timeout(1800)
async def test_benchmark_search_traffic(
    engine_factory, client: AsyncClient, test_project: Project, search_service, app_config
):
    """Report the bytes each retrieval mode reads on a large project."""
    note_count = int(os.environ.get("BASIC_MEMORY_SEARCH_TRAFFIC_NOTES", "300"))
    await seed_notes(search_service, note_count)
    engine, _ = engine_factory

    traffic = [
        await measure_search(engine, client, test_project, mode, text)
        for mode in SearchRetrievalMode
        for text in QUERIES
    ]
    # Without filters Postgres takes the approximate HNSW scan instead of ranking the
    # admitted chunks exactly.
    traffic += [
        await measure_search(
            engine, client, test_project, SearchRetrievalMode.HYBRID, text, filtered=False
        )
        for text in QUERIES
    ]
    report(traffic, note_count, app_config.database_backend.value)
