"""Read-only inspection of one entity's retrieval projections."""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, assert_never

from basic_memory import db
from basic_memory.file_utils import FileError
from basic_memory.indexing.note_content_reconciler import note_content_state_from_model
from basic_memory.indexing.note_content_reconciliation import (
    NoteContentState,
    NoteContentWriteStatus,
)
from basic_memory.models import Entity
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.repository.search_repository_base import (
    ChunkManifestRow,
    FUSION_FORMULA_VERSION,
    SearchRepositoryBase,
)
from basic_memory.repository.search_trace import (
    FinalResultEntry,
    QueryMeta,
    QueryTrace,
    RerankerConfigSummary,
    SearchTraceCollector,
    finalize_query_trace,
)
from basic_memory.repository.semantic_chunking import (
    build_entity_fingerprint,
    build_vector_chunk_records,
)
from basic_memory.schemas.inspect import ChunkStatus
from basic_memory.schemas.search import SearchQuery
from basic_memory.services.file_service import FileService
from basic_memory.services.search_service import SearchService, entity_embeddings_enabled


@dataclass(frozen=True, slots=True)
class ConfiguredVectorIdentity:
    """The active vector identity, or an explicit disabled inspection state."""

    embedding_model: str
    vector_index: str
    semantic_enabled: bool = True


@dataclass(frozen=True, slots=True)
class CurrentSourceHashes:
    """Current chunk and entity hashes derived from the search rows."""

    by_chunk_key: Mapping[str, str]
    entity_fingerprint: str


@dataclass(frozen=True, slots=True)
class InspectedChunk:
    """One stored manifest chunk with its derived inspection status."""

    stored_row: ChunkManifestRow
    ordinal: int
    status: ChunkStatus


@dataclass(frozen=True, slots=True)
class InspectedSearchRow:
    """One current search row and the stored chunks derived from it."""

    search_row: SearchIndexRow
    chunks: tuple[InspectedChunk, ...]


@dataclass(frozen=True, slots=True)
class InspectedDetachedSearchRow:
    """Stored chunks whose source search row no longer exists."""

    row_type: str
    row_id: int
    chunks: tuple[InspectedChunk, ...]


@dataclass(frozen=True, slots=True)
class ChunkReadiness:
    """Mutually exclusive readiness counts for one entity manifest.

    ``missing`` counts current chunks with no manifest row at all — they never enter the
    stored-row comparison, so ``total`` (stored rows) cannot see them.
    """

    total: int
    ready: int
    pending: int
    stale: int
    orphaned: int
    missing: int


@dataclass(frozen=True, slots=True)
class ChunkFresh:
    """The file, search rows, and stored chunks agree."""

    value: Literal["fresh"] = "fresh"


@dataclass(frozen=True, slots=True)
class ChunkIndexBehindRows:
    """Stored chunks trail current rows, cannot serve them, or do not cover them all."""

    entity_fingerprint_indexed: str | tuple[str, ...] | None
    entity_fingerprint_current: str
    missing_chunk_count: int = 0
    value: Literal["index_behind_rows"] = "index_behind_rows"


@dataclass(frozen=True, slots=True)
class ChunkNotIndexed:
    """No search projection exists for this entity yet (pre-index or cleared for reindex)."""

    value: Literal["not_indexed"] = "not_indexed"


@dataclass(frozen=True, slots=True)
class FileFreshnessEvidence:
    """File and note-content checksums used to diagnose row freshness."""

    entity_checksum: str | None
    current_file_checksum: str | None
    db_checksum: str | None
    file_checksum: str | None
    file_write_status: NoteContentWriteStatus | None


@dataclass(frozen=True, slots=True)
class ChunkRowsBehindFile:
    """Search rows were derived from bytes older than the canonical file."""

    evidence: FileFreshnessEvidence
    value: Literal["rows_behind_file"] = "rows_behind_file"


@dataclass(frozen=True, slots=True)
class ChunkFreshnessUnknown:
    """The file could not be read and lineage cannot prove its relation to the rows.

    Row-to-index divergence is independent evidence. It remains visible through
    ``EntityChunkInspection.stale`` and the indexed/current fingerprint fields.
    """

    evidence: FileFreshnessEvidence
    value: Literal["unknown"] = "unknown"


type EntityChunkFreshness = (
    ChunkFresh
    | ChunkNotIndexed
    | ChunkIndexBehindRows
    | ChunkRowsBehindFile
    | ChunkFreshnessUnknown
)


@dataclass(frozen=True, slots=True)
class EntityChunkInspection:
    """Complete note-level retrieval inspection result.

    ``stale`` reports whether stored chunks trail or cannot serve current search rows. It can
    be true while ``freshness`` is unknown because unreadable file bytes leave the upstream
    file-to-row relationship unresolved.
    """

    entity: Entity
    configured_identity: ConfiguredVectorIdentity
    readiness: ChunkReadiness
    entity_fingerprint_indexed: str | tuple[str, ...] | None
    entity_fingerprint_current: str
    stale: bool
    freshness: EntityChunkFreshness
    rows: tuple[InspectedSearchRow, ...]
    detached: tuple[InspectedDetachedSearchRow, ...]


def classify_chunk_status(
    stored_row: ChunkManifestRow,
    current_source_hashes: CurrentSourceHashes,
    configured_identity: ConfiguredVectorIdentity,
    physical_chunk_keys: set[str] | None,
) -> ChunkStatus:
    """Classify one stored chunk against current sources and configured retrieval identity.

    ``physical_chunk_keys`` names the manifest chunks whose built-in physical vector row
    is live; ``None`` means physical storage is not inspectable (semantic disabled or an
    external index) and status stays manifest-only.
    """
    if configured_identity.semantic_enabled and (
        stored_row.embedding_model != configured_identity.embedding_model
        or stored_row.vector_index != configured_identity.vector_index
    ):
        return "orphaned"

    current_source_hash = current_source_hashes.by_chunk_key.get(stored_row.chunk_key)
    if (
        current_source_hash != stored_row.source_hash
        or stored_row.entity_fingerprint != current_source_hashes.entity_fingerprint
    ):
        return "stale"

    match stored_row.embedding_status:
        case "ready":
            # Trigger: the manifest claims ready but the built-in physical vector row is
            # gone or carries a different source_hash.
            # Why: retrieval joins the manifest to physical storage, so a missing join
            # partner can never be served — get_embedding_status() counts the same
            # state as orphaned at the project level.
            # Outcome: report the chunk as orphaned instead of ready.
            if physical_chunk_keys is not None and stored_row.chunk_key not in physical_chunk_keys:
                return "orphaned"
            return "ready"
        case "pending":
            return "pending"
        case unexpected:  # pragma: no cover - repository hydration rejects this value
            assert_never(unexpected)


def _summarize_readiness(
    chunks: tuple[InspectedChunk, ...],
    *,
    missing_current_chunks: int,
) -> ChunkReadiness:
    ready = 0
    pending = 0
    stale = 0
    orphaned = 0
    for chunk in chunks:
        match chunk.status:
            case "ready":
                ready += 1
            case "pending":
                pending += 1
            case "stale":
                stale += 1
            case "orphaned":
                orphaned += 1
            case unexpected:  # pragma: no cover - ChunkStatus is exhaustive
                assert_never(unexpected)

    return ChunkReadiness(
        total=len(chunks),
        ready=ready,
        pending=pending,
        stale=stale,
        orphaned=orphaned,
        missing=missing_current_chunks,
    )


def _file_freshness_evidence(
    *,
    entity_checksum: str | None,
    current_file_checksum: str | None,
    note_content: NoteContentState | None,
) -> FileFreshnessEvidence:
    """Collect the portable checksum evidence exposed by the inspection response."""
    return FileFreshnessEvidence(
        entity_checksum=entity_checksum,
        current_file_checksum=current_file_checksum,
        db_checksum=note_content.db_checksum if note_content is not None else None,
        file_checksum=note_content.file_checksum if note_content is not None else None,
        file_write_status=(note_content.file_write_status if note_content is not None else None),
    )


def lineage_shows_rows_behind_file(
    *,
    entity_checksum: str,
    note_content: NoteContentState | None,
) -> bool:
    """Return whether note-content lineage proves that indexed rows trail the file.

    ``synced`` proves that ``file_checksum`` names accepted bytes only when it agrees with
    ``db_checksum``. ``external_change_detected`` instead records the actual unexpected file
    checksum protected by the materialization conflict guard. Pending, writing, and failed
    states retain historical bookkeeping but do not prove which bytes are currently in storage.
    """
    if note_content is None or note_content.file_checksum is None:
        return False

    match note_content.file_write_status:
        case "synced":
            file_checksum_is_observed = note_content.file_checksum == note_content.db_checksum
        case "external_change_detected":
            file_checksum_is_observed = note_content.file_checksum != note_content.db_checksum
        case "pending" | "writing" | "failed":
            file_checksum_is_observed = False
        case unexpected:  # pragma: no cover - NoteContentState validates the persisted status
            assert_never(unexpected)

    return file_checksum_is_observed and note_content.file_checksum != entity_checksum


def derive_chunk_freshness(
    *,
    entity_search_row_present: bool,
    entity_checksum: str | None,
    current_file_checksum: str | None,
    note_content: NoteContentState | None,
    entity_fingerprint_indexed: str | tuple[str, ...] | None,
    entity_fingerprint_current: str,
    index_behind_rows: bool,
    missing_chunk_count: int,
) -> EntityChunkFreshness:
    """Derive note-level freshness from file, row, and chunk evidence."""
    # Trigger: the entity has no search row at all (pre-first-index, or cleared for reindex).
    # Why: with two empty projections every comparison is vacuously "matching", which would
    # report fresh while the exact layer this inspector diagnoses is absent.
    # Outcome: name the missing projection instead of claiming freshness.
    if not entity_search_row_present:
        return ChunkNotIndexed()

    file_evidence = _file_freshness_evidence(
        entity_checksum=entity_checksum,
        current_file_checksum=current_file_checksum,
        note_content=note_content,
    )

    # Trigger: the entity has no final checksum, or current/observed file bytes differ from it.
    # Why: the file feeds search rows, which in turn feed the chunk index.
    # Outcome: report the upstream rows-behind-file divergence even if the manifest is stale too.
    if entity_checksum is None:
        return ChunkRowsBehindFile(evidence=file_evidence)
    if current_file_checksum is not None:
        if current_file_checksum != entity_checksum:
            return ChunkRowsBehindFile(evidence=file_evidence)
    elif lineage_shows_rows_behind_file(
        entity_checksum=entity_checksum,
        note_content=note_content,
    ):
        return ChunkRowsBehindFile(evidence=file_evidence)
    else:
        return ChunkFreshnessUnknown(evidence=file_evidence)

    # Trigger: stored fingerprints or per-chunk source hashes disagree with current rows,
    # or current chunks have no manifest row at all (e.g. only the first shard of an
    # over-limit entity was scheduled).
    # Why: matching fingerprints cannot prove per-chunk agreement or coverage — a stale
    # source hash can retain the current fingerprint, and a missing key enters neither
    # stored-row comparison.
    # Outcome: report the index as behind the rows, with the uncovered count as evidence.
    if index_behind_rows or missing_chunk_count:
        return ChunkIndexBehindRows(
            entity_fingerprint_indexed=entity_fingerprint_indexed,
            entity_fingerprint_current=entity_fingerprint_current,
            missing_chunk_count=missing_chunk_count,
        )
    return ChunkFresh()


async def _load_note_content_state(
    repository: SearchRepository,
    entity: Entity,
) -> NoteContentState | None:
    """Read the project-scoped note-content lineage once for this inspection."""
    note_content_repository = NoteContentRepository(project_id=entity.project_id)
    async with db.scoped_session(repository.session_maker) as session:
        note_content = await note_content_repository.get_by_entity_id(session, entity.id)
    return note_content_state_from_model(note_content) if note_content is not None else None


async def _read_current_file_checksum(
    file_service: FileService,
    entity: Entity,
) -> str | None:
    """Read one project-scoped file checksum, returning absence when storage is unavailable."""
    try:
        return await file_service.compute_checksum(entity.file_path)
    except FileError:
        return None


async def inspect_entity_chunks(
    repository: SearchRepository,
    entity: Entity,
    file_service: FileService,
) -> EntityChunkInspection:
    """Inspect current search rows and stored vector chunks without running retrieval stages."""
    # SQLite FTS can hold duplicate copies of one logical (type, id) row.
    # build_vector_chunk_records() collapses them when chunking, so this view must
    # too — otherwise displayed chunks duplicate while readiness counts each once.
    search_rows_by_key = {
        (row.type, row.id): row for row in await repository.get_entity_search_rows(entity.id)
    }
    stored_rows = await repository.get_entity_chunk_manifest(entity.id)
    physical_chunk_keys = await repository.get_entity_physical_chunk_keys(entity.id)
    note_content = await _load_note_content_state(repository, entity)
    current_file_checksum = await _read_current_file_checksum(file_service, entity)

    current_records = build_vector_chunk_records(list(search_rows_by_key.values())).records
    current_source_hashes = CurrentSourceHashes(
        by_chunk_key={record["chunk_key"]: record["source_hash"] for record in current_records},
        entity_fingerprint=build_entity_fingerprint(current_records),
    )
    semantic_enabled = await repository.semantic_effectively_enabled()
    configured_identity = ConfiguredVectorIdentity(
        # Trigger: semantic retrieval is disabled by config or runtime fallback.
        # Why: dormant provider settings are not required to form a valid embedding
        # identity, and inspection must retain its rows-only diagnostic path.
        # Outcome: expose the disabled state without validating or loading a provider;
        # stored chunks remain manifest-only rather than becoming false orphans.
        embedding_model=(repository.configured_embedding_model if semantic_enabled else "disabled"),
        vector_index=repository.configured_vector_index,
        semantic_enabled=semantic_enabled,
    )

    inspected_chunks: list[InspectedChunk] = []
    chunks_by_search_row: dict[tuple[str, int], list[InspectedChunk]] = {}
    for stored_row in stored_rows:
        row_key = SearchRepositoryBase._parse_chunk_key(stored_row.chunk_key)
        ordinal = int(stored_row.chunk_key.split(":")[2])
        inspected_chunk = InspectedChunk(
            stored_row=stored_row,
            ordinal=ordinal,
            status=classify_chunk_status(
                stored_row,
                current_source_hashes,
                configured_identity,
                physical_chunk_keys,
            ),
        )
        inspected_chunks.append(inspected_chunk)
        chunks_by_search_row.setdefault(row_key, []).append(inspected_chunk)

    indexed_fingerprints = tuple(sorted({row.entity_fingerprint for row in stored_rows}))
    if not indexed_fingerprints:
        indexed_fingerprint: str | tuple[str, ...] | None = None
    elif len(indexed_fingerprints) == 1:
        indexed_fingerprint = indexed_fingerprints[0]
    else:
        indexed_fingerprint = indexed_fingerprints

    fingerprint_mismatch = any(
        fingerprint != current_source_hashes.entity_fingerprint
        for fingerprint in indexed_fingerprints
    )
    # Trigger: any stored chunk is not currently retrievable under the active identity.
    # Why: semantic hydration admits only ready manifest rows; pending, stale, and
    # orphaned rows all leave the index behind the current retrieval projection.
    # Outcome: note-level freshness cannot contradict the readiness breakdown.
    index_behind_rows = fingerprint_mismatch or any(
        chunk.status != "ready" for chunk in inspected_chunks
    )
    # A current chunk with no manifest row never enters the stored-row loop above, so
    # count the uncovered remainder explicitly (e.g. shards beyond the scheduling limit).
    # With semantic indexing off — by config, by the runtime keyword-only fallback, or
    # by this note's own embed opt-out — no chunks are expected at all, so an empty
    # manifest is not missing coverage.
    if entity_embeddings_enabled(entity) and semantic_enabled:
        stored_chunk_keys = {stored_row.chunk_key for stored_row in stored_rows}
        missing_chunk_count = sum(
            1
            for chunk_key in current_source_hashes.by_chunk_key
            if chunk_key not in stored_chunk_keys
        )
    else:
        missing_chunk_count = 0
    inspected_chunk_tuple = tuple(inspected_chunks)
    return EntityChunkInspection(
        entity=entity,
        configured_identity=configured_identity,
        readiness=_summarize_readiness(
            inspected_chunk_tuple,
            missing_current_chunks=missing_chunk_count,
        ),
        entity_fingerprint_indexed=indexed_fingerprint,
        entity_fingerprint_current=current_source_hashes.entity_fingerprint,
        stale=index_behind_rows or missing_chunk_count > 0,
        freshness=derive_chunk_freshness(
            entity_search_row_present=("entity", entity.id) in search_rows_by_key,
            entity_checksum=entity.checksum,
            current_file_checksum=current_file_checksum,
            note_content=note_content,
            entity_fingerprint_indexed=indexed_fingerprint,
            entity_fingerprint_current=current_source_hashes.entity_fingerprint,
            index_behind_rows=index_behind_rows,
            missing_chunk_count=missing_chunk_count,
        ),
        rows=tuple(
            InspectedSearchRow(
                search_row=search_row,
                chunks=tuple(chunks_by_search_row.get((search_row.type, search_row.id), ())),
            )
            for search_row in search_rows_by_key.values()
        ),
        detached=tuple(
            InspectedDetachedSearchRow(
                row_type=row_type,
                row_id=row_id,
                chunks=tuple(chunks),
            )
            for (row_type, row_id), chunks in chunks_by_search_row.items()
            if (row_type, row_id) not in search_rows_by_key
        ),
    )


async def explain_query(
    search_service: SearchService,
    query: SearchQuery,
    *,
    limit: int,
    offset: int,
) -> QueryTrace:
    """Run one real search and freeze its execution-native retrieval trace."""
    collector = SearchTraceCollector()
    started_at = time.perf_counter()
    results = await search_service.search(
        query,
        limit=limit,
        offset=offset,
        trace=collector,
    )
    total_ms = (time.perf_counter() - started_at) * 1000

    repository = search_service.repository
    mode = query.retrieval_mode.value
    reranker_model = repository.configured_reranker_model
    rerank_applied = collector.rerank is not None
    if rerank_applied:
        rerank_skipped_reason = None
    elif mode == "fts":
        rerank_skipped_reason = "fts_mode"
    elif reranker_model is None:
        rerank_skipped_reason = "disabled"
    elif not results:
        # Trigger: the final page is empty while earlier stages captured candidates.
        # Why: hydrated chunks can still lose their whole row to the similarity
        # threshold, filters, or a missing search row — only candidates that survive
        # ranking can make an offset page "out of range"; anything less is a
        # genuinely empty retrieval, even with hydrated chunks in the trace.
        # Outcome: distinguish the empty page from an empty ranked candidate set.
        if collector.fusion is not None:
            ranked_candidates = len(collector.fusion.entries)
        elif collector.vector is not None:
            served_rows = {match.key for match in collector.vector.chunk_matches}
            ranked_candidates = (
                len(served_rows)
                - len(collector.vector.threshold_rejections)
                - len(collector.vector.filter_rejections)
                - len(collector.vector.missing_search_rows)
            )
        else:
            ranked_candidates = 0
        rerank_skipped_reason = "page_out_of_range" if ranked_candidates > 0 else "no_candidates"
    else:
        rerank_skipped_reason = "not_applied"

    candidate_limit = collector.vector.candidate_limit if collector.vector is not None else limit
    effective_min_similarity = (
        query.min_similarity
        if query.min_similarity is not None
        else repository.configured_min_similarity
    )
    meta = QueryMeta(
        # Captured by search() from the exact prepared query it executed; absent only
        # when preparation found no criteria and the search short-circuited.
        query_text=(
            collector.executed_query_description
            if collector.executed_query_description is not None
            else "(unsatisfiable query: no criteria)"
        ),
        retrieval_mode=mode,
        limit=limit,
        offset=offset,
        project_id=repository.project_id,
        candidate_limit=candidate_limit,
        rerank_pool_size=(collector.rerank.pool_size if collector.rerank is not None else 0),
        embedding_model=repository.configured_embedding_model,
        vector_index=repository.configured_vector_index,
        fusion_formula_version=FUSION_FORMULA_VERSION,
        min_similarity=effective_min_similarity,
        min_similarity_source=("query" if query.min_similarity is not None else "config"),
        reranker=RerankerConfigSummary(
            enabled=reranker_model is not None,
            model=reranker_model,
            candidates=repository.configured_reranker_candidates,
        ),
        rerank_applied=rerank_applied,
        rerank_skipped_reason=rerank_skipped_reason,
        total_ms=total_ms,
    )
    final: list[FinalResultEntry] = []
    for rank, row in enumerate(results, start=1):
        # entity_id is nullable storage on both backends (legacy/diagnostic rows);
        # a missing owner degrades external-id enrichment, never the whole trace.
        final.append(
            FinalResultEntry(
                key=(row.type, row.id),
                entity_id=row.entity_id,
                title=row.title,
                permalink=row.permalink,
                file_path=row.file_path,
                final_rank=offset + rank,
                final_score=row.score or 0.0,
            )
        )
    return finalize_query_trace(collector, meta, final, mode)
