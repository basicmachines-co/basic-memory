"""Real-model smoke coverage for the local FastEmbed reranker."""

from __future__ import annotations

import math
from typing import cast

import pytest

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.fastembed_rerank_provider import FastEmbedRerankProvider
from basic_memory.repository.rerank_provider_factory import create_rerank_provider
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode

from semantic.conftest import SearchCombo, _create_fastembed_provider, create_search_service
from semantic.corpus import RankingDocument, seed_ranking_documents


SMOKE_QUERY = "How should I fill and heat a moka pot to brew coffee?"
SMOKE_DOCUMENTS = (
    RankingDocument(
        title="Moka Pot Brewing Guide",
        permalink="rerank-smoke/moka-pot-brewing",
        content=(
            "Fill the lower chamber with water just below the safety valve. Add finely ground "
            "coffee to the filter basket without tamping, assemble the moka pot, and heat it "
            "gently until brewed coffee reaches the upper chamber."
        ),
    ),
    RankingDocument(
        title="Moka Pot Gasket Replacement",
        permalink="rerank-smoke/moka-pot-gasket",
        content=(
            "Replace a worn rubber gasket when a moka pot leaks around the middle seam. Remove "
            "the old seal, clean the threads, and press the new gasket into its groove."
        ),
    ),
    RankingDocument(
        title="Cold Brew Storage",
        permalink="rerank-smoke/cold-brew-storage",
        content=(
            "Keep concentrated cold brew coffee refrigerated in a sealed glass bottle and "
            "dilute each serving with water or milk."
        ),
    ),
    RankingDocument(
        title="Espresso Grinder Calibration",
        permalink="rerank-smoke/espresso-grinder",
        content=(
            "Adjust an espresso grinder one step at a time and purge retained coffee before "
            "timing the next shot."
        ),
    ),
    RankingDocument(
        title="Reset a Locked Account",
        permalink="rerank-smoke/account-reset",
        content=(
            "An administrator can unlock a user account after verifying identity and revoke "
            "old login sessions before issuing a password reset link."
        ),
    ),
    RankingDocument(
        title="Apply a Database Migration",
        permalink="rerank-smoke/database-migration",
        content=(
            "Back up the database, apply the schema migration in a transaction, and verify the "
            "new index before serving application traffic."
        ),
    ),
)


@pytest.mark.asyncio
@pytest.mark.semantic
async def test_real_fastembed_reranker_scores_and_hybrid_search(
    sqlite_engine_factory,
    tmp_path,
) -> None:
    """The default cross-encoder should deterministically promote the relevant note."""
    config = BasicMemoryConfig(
        env="test",
        projects={"bench-project": str(tmp_path)},
        default_project="bench-project",
        database_backend=DatabaseBackend.SQLITE,
        semantic_search_enabled=True,
        reranker_enabled=True,
    )
    provider = create_rerank_provider(config)
    assert isinstance(provider, FastEmbedRerankProvider)

    rerank_documents = [f"{document.content}\n{document.title}" for document in SMOKE_DOCUMENTS]
    first_scores = await provider.rerank(SMOKE_QUERY, rerank_documents)
    second_scores = await provider.rerank(SMOKE_QUERY, rerank_documents)

    assert first_scores == second_scores
    assert all(math.isfinite(score) and 0.0 <= score <= 1.0 for score in first_scores)
    assert max(range(len(first_scores)), key=first_scores.__getitem__) == 0

    combo = SearchCombo("sqlite-fastembed-rerank", DatabaseBackend.SQLITE, "fastembed", 384)
    search_service = await create_search_service(
        sqlite_engine_factory,
        combo,
        tmp_path,
        embedding_provider=_create_fastembed_provider(),
        reranker_enabled=True,
    )
    repository = cast(SQLiteSearchRepository, search_service.repository)
    assert repository._rerank_provider is provider
    await seed_ranking_documents(search_service, SMOKE_DOCUMENTS)

    results = await search_service.search(
        SearchQuery(
            text=SMOKE_QUERY,
            retrieval_mode=SearchRetrievalMode.HYBRID,
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=len(SMOKE_DOCUMENTS),
    )

    assert results
    assert results[0].permalink == SMOKE_DOCUMENTS[0].permalink
