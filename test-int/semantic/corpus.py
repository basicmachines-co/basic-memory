"""Shared corpus definitions for semantic search benchmarks.

Provides topic terms, content builder, and query suites used by both
quality benchmarks and coverage tests. Content is designed to produce
realistic overlap between topics — authentication touches sessions AND
databases, sync touches file watching AND agent orchestration — so that
embedding quality actually differentiates providers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


# --- Topic vocabulary ---
# Each topic has primary terms (strongly associated) and secondary terms
# (shared with other topics to create realistic overlap).

TOPIC_TERMS: dict[str, list[str]] = {
    "auth": ["authentication", "session", "token", "oauth", "refresh", "login"],
    "database": ["database", "migration", "schema", "sqlite", "postgres", "index"],
    "sync": ["sync", "filesystem", "watcher", "checksum", "reindex", "changes"],
    "agent": ["agent", "memory", "context", "prompt", "retrieval", "tooling"],
}

TOPIC_NAMES = list(TOPIC_TERMS.keys())


# --- Content templates ---
# Each topic has multiple content variants with realistic prose that
# overlaps with neighboring topics. This prevents trivial keyword-only
# matching and forces embeddings to disambiguate meaning.

TOPIC_CONTENT_TEMPLATES: dict[str, list[str]] = {
    "auth": [
        (
            "## Authentication Architecture\n\n"
            "Our authentication system uses JWT tokens stored in HTTP-only cookies. "
            "When a user logs in, the server validates credentials against the database "
            "and issues a signed access token plus a longer-lived refresh token. "
            "Session state is minimal — we store only the user ID and expiration. "
            "The OAuth 2.1 flow handles third-party providers like GitHub and Google. "
            "Token refresh happens transparently: the client detects a 401 response "
            "and replays the request after obtaining a new access token."
        ),
        (
            "## Session Management Design\n\n"
            "Sessions are tracked server-side with a Redis-backed store. Each login "
            "creates a session record indexed by a random session ID. The session "
            "contains the user profile, active permissions, and the timestamp of last "
            "activity. Idle sessions expire after 30 minutes. We chose server-side "
            "sessions over stateless JWT for revocation support — when a user changes "
            "their password, all existing sessions are invalidated immediately."
        ),
        (
            "## OAuth Integration Guide\n\n"
            "Third-party authentication follows the OAuth 2.1 authorization code flow "
            "with PKCE. The client generates a code verifier, redirects to the provider, "
            "and exchanges the authorization code for tokens on the callback. We support "
            "GitHub, Google, and custom OIDC providers. Token storage uses encrypted "
            "cookies with SameSite=Strict. Refresh tokens are rotated on each use to "
            "limit the window of compromise."
        ),
        (
            "## Security Token Lifecycle\n\n"
            "Access tokens are short-lived (15 minutes) JWTs containing the user ID, "
            "roles, and a session fingerprint. Refresh tokens are opaque strings stored "
            "in the database with a 30-day expiration. On each refresh, the old token is "
            "revoked and a new one issued. We maintain a denylist of revoked tokens "
            "checked at middleware level. Password changes trigger a full session "
            "invalidation cascade across all devices."
        ),
    ],
    "database": [
        (
            "## Database Migration Strategy\n\n"
            "We use Alembic for schema migrations with auto-generation from SQLAlchemy "
            "models. Each migration runs inside a transaction and is tested against both "
            "SQLite and Postgres before merging. The migration naming convention is "
            "descriptive: `add_user_roles_table`, `alter_entity_metadata_column`. "
            "Rollbacks are supported for the last 5 migrations. Large data migrations "
            "run as background tasks to avoid blocking the API."
        ),
        (
            "## Query Optimization Notes\n\n"
            "The search index uses a GIN index on the tsvector column for full-text "
            "search. We found that combining text search with a B-tree index on the "
            "created_at column reduces query time by 60% for time-filtered searches. "
            "The entity metadata column uses JSONB with a GIN index for flexible "
            "filtering. Connection pooling is handled by SQLAlchemy's async engine "
            "with a pool size of 10 and overflow of 20."
        ),
        (
            "## Schema Design Decisions\n\n"
            "Entities use a single-table design with a polymorphic entity_type column. "
            "The search_index table is denormalized for query performance — it stores "
            "pre-computed tsvector data and flattened metadata. Relations use a join "
            "table with source_id and target_id foreign keys. We chose JSONB for "
            "entity_metadata over separate attribute tables because the query patterns "
            "favor flexible filtering over strict schema enforcement."
        ),
        (
            "## SQLite to Postgres Migration\n\n"
            "The migration from SQLite to Postgres required adapting FTS5 virtual "
            "tables to tsvector/tsquery. SQLite's MATCH syntax maps to Postgres "
            "plainto_tsquery for simple searches. Ranking uses ts_rank_cd instead of "
            "SQLite's bm25(). The biggest challenge was handling concurrent writes — "
            "SQLite's WAL mode is single-writer, while Postgres needs explicit locking "
            "strategies for upsert operations on the search index."
        ),
    ],
    "sync": [
        (
            "## File Synchronization Engine\n\n"
            "The sync engine watches the filesystem for changes using platform-native "
            "APIs (FSEvents on macOS, inotify on Linux). When a file change is detected, "
            "the engine computes a content hash and compares it against the stored "
            "checksum. Changed files are queued for re-parsing and re-indexing. The "
            "queue processes files in dependency order — if a relation target is "
            "modified, the source entity is also reindexed to update its links."
        ),
        (
            "## Incremental Reindex Design\n\n"
            "Rather than rebuilding the entire index on each change, we maintain a "
            "change log that tracks which entities need reindexing. The reindex process "
            "reads the markdown file, extracts observations and relations, and updates "
            "the search index and knowledge graph in a single transaction. Checksums "
            "are computed using xxhash for speed. Files that haven't changed since the "
            "last sync are skipped entirely based on mtime + hash comparison."
        ),
        (
            "## Conflict Resolution Strategy\n\n"
            "When the same file is modified both locally and remotely, we use a "
            "last-writer-wins strategy with conflict markers. The sync coordinator "
            "detects conflicts by comparing the base hash (from the last successful "
            "sync) against both the local and remote versions. If the content diverges, "
            "a .conflict file is created alongside the original. The filesystem watcher "
            "picks up the conflict file and flags it for user resolution."
        ),
        (
            "## Watch Mode Architecture\n\n"
            "The file watcher runs as a background asyncio task. It debounces rapid "
            "filesystem events (editor save + temp file creation) using a 500ms window. "
            "Batched changes are processed in topological order based on the relation "
            "graph. The watcher maintains a bloom filter of recently-seen paths to "
            "avoid redundant hash computations. On startup, a full reconciliation pass "
            "compares the filesystem state against the database to catch any changes "
            "that occurred while the watcher was offline."
        ),
    ],
    "agent": [
        (
            "## Agent Memory Architecture\n\n"
            "The agent maintains long-term memory through a knowledge graph stored as "
            "linked markdown files. Each conversation generates observations that are "
            "written to notes and indexed for semantic retrieval. Context is built by "
            "traversing the knowledge graph from relevant entry points, collecting "
            "observations and relations up to a configurable depth. This gives the "
            "agent persistent memory across sessions without requiring the full "
            "conversation history."
        ),
        (
            "## Context Window Management\n\n"
            "When the context window approaches its limit, the agent must prioritize "
            "which information to retain. We use a relevance scoring function that "
            "combines recency (recently accessed notes score higher), connectivity "
            "(notes with more relations are more central), and semantic similarity "
            "to the current query. The build_context tool traverses the knowledge "
            "graph breadth-first, scoring each node and pruning low-relevance branches."
        ),
        (
            "## Tool Orchestration Patterns\n\n"
            "The agent coordinates multiple MCP tools in a planning-execution loop. "
            "First, build_context retrieves relevant background knowledge. Then, "
            "search_notes finds specific information needed for the task. The agent "
            "writes new observations using write_note, creating links back to source "
            "materials via relations. This read-think-write cycle ensures that each "
            "interaction enriches the knowledge graph for future sessions."
        ),
        (
            "## Prompt Engineering for Memory Retrieval\n\n"
            "Effective memory retrieval requires careful prompt design. The agent "
            "uses memory:// URLs to reference specific notes and topics. The "
            "build_context tool accepts depth and timeframe parameters to control "
            "how much context is loaded. For complex tasks, the agent builds context "
            "incrementally — starting with a broad topic scan, then narrowing to "
            "specific entities as the task requirements become clearer."
        ),
    ],
}


# --- Cross-topic content (creates realistic overlap) ---
# These notes deliberately blend vocabulary from multiple topics,
# making them hard for FTS alone to classify correctly.

CROSS_TOPIC_TEMPLATES: list[tuple[str, str]] = [
    (
        "auth",
        (
            "## Database-Backed Authentication\n\n"
            "Token storage relies on the database layer. Refresh tokens are stored "
            "in a dedicated table with columns for the token hash, user ID, expiration, "
            "and device fingerprint. The schema migration that created this table also "
            "added a GIN index on the user_id column for fast lookup. When the sync "
            "engine detects a user profile change, it triggers a session revalidation "
            "to ensure cached permissions stay consistent."
        ),
    ),
    (
        "database",
        (
            "## Search Index Synchronization\n\n"
            "The search index must stay in sync with the filesystem. When a markdown "
            "file is modified, the sync watcher triggers a reindex of the corresponding "
            "entity. The database transaction includes updating the search_index table "
            "tsvector column, refreshing the entity metadata JSONB, and recalculating "
            "relation links. If the file was deleted, we cascade-delete the search index "
            "entry and orphan any dangling relations."
        ),
    ),
    (
        "sync",
        (
            "## Agent-Driven Sync Coordination\n\n"
            "The agent can trigger manual sync operations through the MCP sync_status "
            "tool. When context building reveals stale data (notes modified on disk "
            "but not yet reindexed), the agent requests a targeted reindex of the "
            "affected entities. The sync coordinator prioritizes agent-requested "
            "reindexes over background filesystem watcher events to minimize latency "
            "for interactive sessions."
        ),
    ),
    (
        "agent",
        (
            "## Authentication-Aware Agent Context\n\n"
            "In multi-user deployments, the agent's context is scoped by the "
            "authenticated user's permissions. The JWT token includes project access "
            "claims that the knowledge client uses to filter search results. When "
            "building context, the agent only traverses notes belonging to projects "
            "the user has access to. Session expiration during a long agent task "
            "triggers a graceful context save before re-authentication."
        ),
    ),
]


# --- Query case types ---


@dataclass(frozen=True)
class QueryCase:
    """A single benchmark query with its expected topic."""

    text: str
    expected_topic: str


# --- Query suites ---
# Lexical queries use keywords that appear in the content but require
# disambiguation when topics share vocabulary.
# Paraphrase queries rephrase concepts without using any topic keywords.

LEXICAL_QUERIES: list[QueryCase] = [
    QueryCase(text="JWT token refresh login OAuth", expected_topic="auth"),
    QueryCase(text="session cookie authentication credentials", expected_topic="auth"),
    QueryCase(text="schema migration alembic database table", expected_topic="database"),
    QueryCase(text="tsvector GIN index query optimization", expected_topic="database"),
    QueryCase(text="filesystem watcher inotify checksum reindex", expected_topic="sync"),
    QueryCase(text="file change detection sync queue", expected_topic="sync"),
    QueryCase(text="agent knowledge graph memory context", expected_topic="agent"),
    QueryCase(text="MCP tool orchestration prompt retrieval", expected_topic="agent"),
]

PARAPHRASE_QUERIES: list[QueryCase] = [
    QueryCase(
        text="How do we verify user identity and manage their active sessions?",
        expected_topic="auth",
    ),
    QueryCase(
        text="What happens when someone's credential expires and needs renewal?",
        expected_topic="auth",
    ),
    QueryCase(
        text="How is the data storage layer structured and how do we evolve it over time?",
        expected_topic="database",
    ),
    QueryCase(
        text="What techniques make our search queries faster on large datasets?",
        expected_topic="database",
    ),
    QueryCase(
        text="How do we detect when local documents have been edited and need processing?",
        expected_topic="sync",
    ),
    QueryCase(
        text="What strategy handles conflicting edits from multiple sources?",
        expected_topic="sync",
    ),
    QueryCase(
        text="How does the AI assistant remember things between separate conversations?",
        expected_topic="agent",
    ),
    QueryCase(
        text="What approach lets the assistant build up relevant background before acting?",
        expected_topic="agent",
    ),
]

QUERY_SUITES: dict[str, list[QueryCase]] = {
    "lexical": LEXICAL_QUERIES,
    "paraphrase": PARAPHRASE_QUERIES,
}


# --- Content builder ---


def build_benchmark_content(topic: str, terms: list[str], note_index: int) -> str:
    """Build markdown content for a benchmark note.

    Uses realistic prose templates with cross-topic vocabulary overlap.
    Each note gets a different template variant to increase content diversity.
    """
    templates = TOPIC_CONTENT_TEMPLATES[topic]
    template = templates[note_index % len(templates)]

    # Add a light keyword section for FTS discoverability, but keep it
    # secondary to the prose content so embedding quality matters.
    keyword_line = ", ".join(terms)

    return f"""---
tags: [benchmark, {topic}]
status: active
---
# {topic.title()} Note {note_index}

{template}

## Keywords
Related concepts: {keyword_line}.
"""


# --- Seeding ---


async def seed_benchmark_notes(search_service, note_count: int = 240):
    """Seed the search index with benchmark notes across all topics.

    Notes are assigned topics round-robin and given permalinks like
    ``bench/{topic}-{index:05d}`` so relevance can be checked by
    inspecting the permalink prefix.

    Approximately 15% of notes use cross-topic content templates to
    create realistic vocabulary overlap between topics.
    """
    entities = []
    rng = random.Random(42)  # Deterministic for reproducibility

    # Pre-compute which note indices get cross-topic content
    cross_topic_indices = set(
        rng.sample(range(note_count), k=min(note_count // 7, len(CROSS_TOPIC_TEMPLATES) * 8))
    )

    cross_topic_cycle = 0

    for note_index in range(note_count):
        topic = TOPIC_NAMES[note_index % len(TOPIC_NAMES)]
        terms = TOPIC_TERMS[topic]
        permalink = f"bench/{topic}-{note_index:05d}"

        # Decide content: cross-topic or standard
        if note_index in cross_topic_indices:
            ct_topic, ct_content = CROSS_TOPIC_TEMPLATES[
                cross_topic_cycle % len(CROSS_TOPIC_TEMPLATES)
            ]
            # Cross-topic notes still belong to the round-robin topic for relevance checking,
            # but their content blends vocabulary from the ct_topic.
            keyword_line = ", ".join(terms)
            content = f"""---
tags: [benchmark, {topic}]
status: active
---
# {topic.title()} Note {note_index}

{ct_content}

## Keywords
Related concepts: {keyword_line}.
"""
            cross_topic_cycle += 1
        else:
            content = build_benchmark_content(topic, terms, note_index)

        entity = await search_service.entity_repository.create(
            {
                "title": f"{topic.title()} Benchmark Note {note_index}",
                "note_type": "benchmark",
                "entity_metadata": {"tags": ["benchmark", topic], "status": "active"},
                "content_type": "text/markdown",
                "permalink": permalink,
                "file_path": f"{permalink}.md",
            }
        )
        await search_service.index_entity_data(entity, content=content)

        # Sync vector embeddings when semantic search is enabled
        if search_service.repository._semantic_enabled:
            await search_service.sync_entity_vectors(entity.id)

        entities.append(entity)

    return entities
