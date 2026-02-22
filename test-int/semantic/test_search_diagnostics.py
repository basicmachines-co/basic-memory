"""Diagnostic tests for semantic search quality issues.

These tests isolate specific problems with the search pipeline:
1. Similarity score compression — cosine distances map to a narrow similarity band
2. Observation noise — context-free observations match too broadly
3. RRF fusion behavior — how FTS and vector scores interact
4. Min-similarity threshold effectiveness
"""

from __future__ import annotations

import pytest

from basic_memory.config import DatabaseBackend
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode

from semantic.conftest import (
    SearchCombo,
    create_search_service,
    _create_fastembed_provider,
    skip_if_needed,
)


# Use a single combo for focused diagnostics — sqlite + fastembed
DIAG_COMBO = SearchCombo("sqlite-fastembed", DatabaseBackend.SQLITE, "fastembed", 384)


# --- Helpers ---


async def seed_diagnostic_notes(search_service):
    """Seed a small, controlled corpus where we know exactly what should match."""
    notes = [
        {
            "title": "Authentication Flow",
            "permalink": "diag/auth-flow",
            "content": (
                "---\ntags: [auth]\n---\n"
                "# Authentication Flow\n\n"
                "Users log in with email and password. The server validates credentials, "
                "issues a JWT access token and a refresh token. Tokens are stored in "
                "HTTP-only cookies. OAuth 2.1 is supported for GitHub and Google SSO.\n\n"
                "## Observations\n"
                "- [design] JWT tokens expire after 15 minutes\n"
                "- [design] Refresh tokens are rotated on each use\n"
            ),
        },
        {
            "title": "Database Schema",
            "permalink": "diag/db-schema",
            "content": (
                "---\ntags: [database]\n---\n"
                "# Database Schema\n\n"
                "The entity table uses a polymorphic type column. Metadata is stored as "
                "JSONB with GIN indexes. The search_index table is denormalized for query "
                "performance. Alembic manages all schema migrations.\n\n"
                "## Observations\n"
                "- [design] Single-table inheritance for entities\n"
                "- [design] JSONB metadata with GIN indexes\n"
            ),
        },
        {
            "title": "File Sync Engine",
            "permalink": "diag/sync-engine",
            "content": (
                "---\ntags: [sync]\n---\n"
                "# File Sync Engine\n\n"
                "The sync engine watches the filesystem for changes using FSEvents on macOS. "
                "When a file changes, the engine computes a content hash and compares it "
                "against the stored checksum. Changed files are queued for re-parsing and "
                "re-indexing.\n\n"
                "## Observations\n"
                "- [design] FSEvents on macOS, inotify on Linux\n"
                "- [design] Content hash comparison for change detection\n"
            ),
        },
        {
            "title": "Recipe for Chocolate Cake",
            "permalink": "diag/chocolate-cake",
            "content": (
                "---\ntags: [recipes]\n---\n"
                "# Recipe for Chocolate Cake\n\n"
                "Preheat oven to 350F. Mix 2 cups flour, 1 cup sugar, 3/4 cup cocoa powder. "
                "Add eggs, buttermilk, and melted butter. Bake for 30 minutes.\n\n"
                "## Observations\n"
                "- [ingredient] Dark cocoa powder gives richer flavor\n"
                "- [technique] Buttermilk makes the cake moist\n"
            ),
        },
        {
            "title": "Garden Planning Guide",
            "permalink": "diag/garden-planning",
            "content": (
                "---\ntags: [gardening]\n---\n"
                "# Garden Planning Guide\n\n"
                "Start seeds indoors 6 weeks before last frost. Tomatoes need full sun "
                "and well-drained soil. Companion planting with basil repels pests. "
                "Water deeply once per week rather than shallow daily watering.\n\n"
                "## Observations\n"
                "- [technique] Companion planting with basil\n"
                "- [timing] Start seeds 6 weeks before last frost\n"
            ),
        },
    ]

    entities = []
    for note in notes:
        entity = await search_service.entity_repository.create(
            {
                "title": note["title"],
                "note_type": "note",
                "entity_metadata": {"tags": note.get("tags", [])},
                "content_type": "text/markdown",
                "permalink": note["permalink"],
                "file_path": f"{note['permalink']}.md",
            }
        )
        await search_service.index_entity_data(entity, content=note["content"])
        await search_service.sync_entity_vectors(entity.id)
        entities.append(entity)

    return entities


# --- Test: Similarity score distribution ---


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_similarity_score_spread(sqlite_engine_factory, tmp_path):
    """Check that similarity scores have meaningful spread between relevant and irrelevant results.

    If the top relevant result and the worst irrelevant result have similar scores,
    the similarity formula is too compressed to be useful.
    """
    skip_if_needed(DIAG_COMBO)
    provider = _create_fastembed_provider()
    service = await create_search_service(
        sqlite_engine_factory, DIAG_COMBO, tmp_path, embedding_provider=provider
    )
    await seed_diagnostic_notes(service)

    # Query clearly about authentication — disable min_similarity to see all results
    results = await service.search(
        SearchQuery(
            text="how does user login and authentication work",
            retrieval_mode=SearchRetrievalMode.VECTOR,
            min_similarity=0.0,
        ),
        limit=5,
    )

    assert len(results) > 0, "Vector search returned no results"

    scores = [(r.permalink, r.score) for r in results]
    print("\nVector search: 'how does user login and authentication work'")
    for permalink, score in scores:
        print(f"  {permalink}: {score:.4f}")

    # The auth note should be the top result
    top_result = results[0]
    assert "auth" in (top_result.permalink or ""), (
        f"Expected auth-related result at top, got: {top_result.permalink}"
    )

    # Check score spread — top relevant vs bottom result
    top_score = results[0].score or 0.0
    bottom_score = results[-1].score or 0.0
    spread = top_score - bottom_score

    print(f"\n  Score spread: {spread:.4f} (top={top_score:.4f}, bottom={bottom_score:.4f})")

    # With the L2→cosine formula, spread should be meaningful (> 0.10)
    assert spread > 0.10, (
        f"Score spread too narrow ({spread:.4f}). "
        f"Similarity formula compresses distances into indistinguishable range."
    )


# --- Test: Observation noise ---


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_observation_noise_vs_entity(sqlite_engine_factory, tmp_path):
    """Check whether short observations dominate over relevant entity results.

    A common issue: observations like "Dark cocoa powder gives richer flavor"
    can match broadly because they lack parent context.
    """
    skip_if_needed(DIAG_COMBO)
    provider = _create_fastembed_provider()
    service = await create_search_service(
        sqlite_engine_factory, DIAG_COMBO, tmp_path, embedding_provider=provider
    )
    await seed_diagnostic_notes(service)

    # Query about database design
    results = await service.search(
        SearchQuery(
            text="database schema design and migrations", retrieval_mode=SearchRetrievalMode.VECTOR
        ),
        limit=10,
    )

    entity_results = [r for r in results if r.type == SearchItemType.ENTITY.value]
    obs_results = [r for r in results if r.type == SearchItemType.OBSERVATION.value]

    print("\nVector search: 'database schema design and migrations'")
    print(f"  Entity results: {len(entity_results)}")
    for r in entity_results:
        print(f"    {r.permalink}: {r.score:.4f}")
    print(f"  Observation results: {len(obs_results)}")
    for r in obs_results[:5]:
        print(f"    {r.permalink}: {r.score:.4f}")

    # The database entity should appear in entity results
    db_entities = [r for r in entity_results if "db-schema" in (r.permalink or "")]
    assert len(db_entities) > 0, "Database schema entity not found in entity results"

    # Check if the database entity outranks irrelevant observations
    if obs_results and db_entities:
        best_db_score = max(r.score or 0.0 for r in db_entities)
        # Count how many observations from OTHER topics outrank the db entity
        irrelevant_obs = [
            r
            for r in obs_results
            if (r.score or 0.0) > best_db_score and "db-schema" not in (r.permalink or "")
        ]
        print(f"\n  Irrelevant observations outranking db entity: {len(irrelevant_obs)}")
        for r in irrelevant_obs:
            print(f"    {r.permalink}: {r.score:.4f}")


# --- Test: RRF fusion — vector vs hybrid comparison ---


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_rrf_fusion_preserves_strong_vector_match(sqlite_engine_factory, tmp_path):
    """When vector gives a strong match and FTS doesn't, hybrid should still surface it.

    This is the core claim of issue #577 — that RRF dilutes strong vector scores.
    Let's verify with a controlled corpus.
    """
    skip_if_needed(DIAG_COMBO)
    provider = _create_fastembed_provider()
    service = await create_search_service(
        sqlite_engine_factory, DIAG_COMBO, tmp_path, embedding_provider=provider
    )
    await seed_diagnostic_notes(service)

    # Paraphrase query — no keywords from the auth content
    query_text = "how do we verify someone's identity and manage their active sessions"

    vector_results = await service.search(
        SearchQuery(text=query_text, retrieval_mode=SearchRetrievalMode.VECTOR),
        limit=5,
    )
    hybrid_results = await service.search(
        SearchQuery(text=query_text, retrieval_mode=SearchRetrievalMode.HYBRID),
        limit=5,
    )
    fts_results = await service.search(
        SearchQuery(text=query_text, retrieval_mode=SearchRetrievalMode.FTS),
        limit=5,
    )

    print(f"\nQuery: '{query_text}'")
    print("\n  FTS results:")
    for r in fts_results[:5]:
        print(f"    {r.permalink}: {r.score:.6f}")

    print("\n  Vector results:")
    for r in vector_results[:5]:
        print(f"    {r.permalink}: {r.score:.4f}")

    print("\n  Hybrid results:")
    for r in hybrid_results[:5]:
        print(f"    {r.permalink}: {r.score:.6f}")

    # Check: if vector found auth at rank 1, does hybrid preserve that?
    vector_top = vector_results[0] if vector_results else None
    vector_top_is_auth = vector_top and "auth" in (vector_top.permalink or "")

    if vector_top_is_auth:
        # Find auth in hybrid results
        hybrid_auth = [r for r in hybrid_results if "auth" in (r.permalink or "")]
        if hybrid_auth:
            hybrid_auth_rank = hybrid_results.index(hybrid_auth[0]) + 1
            print(f"\n  Auth rank — vector: 1, hybrid: {hybrid_auth_rank}")
            # Auth should still be in the top 3 in hybrid mode
            assert hybrid_auth_rank <= 3, (
                f"Hybrid pushed auth from vector rank 1 to hybrid rank {hybrid_auth_rank}. "
                f"RRF dilution confirmed."
            )
        else:
            print("\n  WARNING: Auth found by vector but missing from hybrid results entirely!")


# --- Test: Similarity formula analysis ---


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_similarity_formula_analysis(sqlite_engine_factory, tmp_path):
    """Analyze the raw distance-to-similarity mapping for real queries.

    Production formulas are backend-specific:
    - SQLite: similarity = max(0, 1 - L2²/2) for normalized embeddings
    - Postgres: similarity = max(0, 1 - cosine_distance)
    This test compares old and new mappings for diagnostics.
    """
    skip_if_needed(DIAG_COMBO)
    provider = _create_fastembed_provider()
    service = await create_search_service(
        sqlite_engine_factory, DIAG_COMBO, tmp_path, embedding_provider=provider
    )
    await seed_diagnostic_notes(service)

    queries = [
        "user authentication and login flow",
        "database schema migrations",
        "how to bake a chocolate cake",
        "growing tomatoes in a garden",
    ]

    for query_text in queries:
        # Get raw vector distances by querying at the repository level
        query_embedding = await provider.embed_query(query_text.strip())

        from basic_memory import db as bm_db

        async with bm_db.scoped_session(service.repository.session_maker) as session:
            await service.repository._prepare_vector_session(session)
            raw_rows = await service.repository._run_vector_query(
                session, query_embedding, candidate_limit=20
            )

        print(f"\nQuery: '{query_text}'")
        print(f"  {'chunk_key':<40} {'distance':>10} {'sim_old':>12} {'sim_new':>12}")
        for row in raw_rows[:10]:
            dist = float(row["best_distance"])
            sim_old = 1.0 / (1.0 + max(dist, 0.0))
            # New formula: L2 distance → cosine similarity for normalized embeddings
            sim_new = service.repository._distance_to_similarity(dist)
            print(f"  {row['chunk_key']:<40} {dist:>10.4f} {sim_old:>12.4f} {sim_new:>12.4f}")


# --- Test: min_similarity threshold effectiveness ---


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_min_similarity_filters_noise(sqlite_engine_factory, tmp_path):
    """Verify that min_similarity actually removes low-quality matches."""
    skip_if_needed(DIAG_COMBO)
    provider = _create_fastembed_provider()
    service = await create_search_service(
        sqlite_engine_factory, DIAG_COMBO, tmp_path, embedding_provider=provider
    )
    await seed_diagnostic_notes(service)

    query_text = "user authentication login"

    # Without threshold
    results_no_threshold = await service.search(
        SearchQuery(
            text=query_text,
            retrieval_mode=SearchRetrievalMode.VECTOR,
        ),
        limit=20,
    )

    # With threshold at 0.55
    results_with_threshold = await service.search(
        SearchQuery(
            text=query_text,
            retrieval_mode=SearchRetrievalMode.VECTOR,
            min_similarity=0.55,
        ),
        limit=20,
    )

    # With aggressive threshold at 0.65
    results_aggressive = await service.search(
        SearchQuery(
            text=query_text,
            retrieval_mode=SearchRetrievalMode.VECTOR,
            min_similarity=0.65,
        ),
        limit=20,
    )

    print(f"\nQuery: '{query_text}'")
    print(f"  No threshold: {len(results_no_threshold)} results")
    for r in results_no_threshold:
        print(f"    {r.permalink}: {r.score:.4f}")
    print(f"  min_similarity=0.55: {len(results_with_threshold)} results")
    for r in results_with_threshold:
        print(f"    {r.permalink}: {r.score:.4f}")
    print(f"  min_similarity=0.65: {len(results_aggressive)} results")
    for r in results_aggressive:
        print(f"    {r.permalink}: {r.score:.4f}")

    # Threshold should reduce result count
    assert len(results_with_threshold) <= len(results_no_threshold), (
        "Threshold didn't reduce results"
    )

    # With threshold, remaining results should all be above threshold
    for r in results_with_threshold:
        assert (r.score or 0.0) >= 0.55, f"Result {r.permalink} below threshold: {r.score:.4f}"


# --- Test: Chunking behavior ---


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_chunking_produces_reasonable_chunks(sqlite_engine_factory, tmp_path):
    """Verify that the chunking logic produces chunks with enough context."""
    skip_if_needed(DIAG_COMBO)
    provider = _create_fastembed_provider()
    service = await create_search_service(
        sqlite_engine_factory, DIAG_COMBO, tmp_path, embedding_provider=provider
    )
    repo = service.repository

    # Simulate a typical entity with observations
    text_input = (
        "Authentication Flow\n\n"
        "diag/auth-flow\n\n"
        "# Authentication Flow\n\n"
        "Users log in with email and password. The server validates credentials.\n\n"
        "## Observations\n"
        "- [design] JWT tokens expire after 15 minutes\n"
        "- [design] Refresh tokens are rotated on each use\n"
        "- [design] OAuth 2.1 supported for GitHub and Google\n"
    )

    chunks = repo._split_text_into_chunks(text_input)

    print("\nChunking analysis:")
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i} ({len(chunk)} chars): {chunk[:80]}...")

    # Each bullet should be its own chunk (current behavior)
    bullet_chunks = [c for c in chunks if c.startswith("- [")]
    print(f"\n  Bullet chunks: {len(bullet_chunks)}")
    print(f"  Total chunks: {len(chunks)}")

    # Verify observations are chunked individually
    assert len(bullet_chunks) >= 3, "Expected at least 3 bullet chunks for 3 observations"

    # Check if bullets have any context (they shouldn't in current impl)
    for bc in bullet_chunks:
        has_context = "Authentication" in bc or "auth-flow" in bc
        print(f"  Bullet has parent context: {has_context} — '{bc[:60]}'")
