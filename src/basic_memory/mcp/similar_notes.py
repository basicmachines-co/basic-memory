"""Similar-note advisory: after a create, list the existing notes nearest to it.

Agents writing across sessions know the topic but not whether a note on it already
exists; the closest notes are surfaced and the decision stays with the caller.
"""

import asyncio
import dataclasses
import re
from collections.abc import Callable, Sequence

from httpx import HTTPStatusError

from basic_memory.config import BasicMemoryConfig
from basic_memory.file_utils import remove_frontmatter
from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
)
from basic_memory.schemas.search import (
    SearchItemType,
    SearchQuery,
    SearchResult,
    SearchRetrievalMode,
)

# How many neighbors to surface after a create. Ranking is reliable enough to put the note
# an agent probably meant at the top; a longer list adds noise, not signal.
SIMILAR_NOTES_LIMIT = 3
# The vector index embeds a note's title and opening content as its first chunk, so the
# probe copies that shape and length to land in the same neighborhood as the note itself.
SIMILAR_NOTES_PROBE_CHARS = 900
# The advisory asks "might this be the same note?", a stricter question than search's
# "is this relevant at all?" (semantic_min_similarity, 0.55). Measured on the default
# bge-small-en-v1.5 model (#1718): rewrites of an existing note scored 0.77-0.93 against
# it (0.78-0.87 on the Moby Dick vault, #1259), while the nearest neighbor of a note on an
# unrelated topic topped out at 0.67. Below this floor the list was only noise; near the
# top, a duplicate and a closely related note still share one band, so ranking decides.
# The number holds for that model only; see similar_notes_min_similarity.
SIMILAR_NOTES_MIN_SIMILARITY = 0.70
# Most wikilinks the advisory resolves to recognize notes the new note already links
# to; see find_similar_notes.
SIMILAR_NOTES_LINKED_ALLOWANCE = 10
# A wikilink target, without its alias: [[Target]] or [[Target|alias]].
_WIKILINK_TARGET = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]")


@dataclasses.dataclass(frozen=True, slots=True)
class SimilarNote:
    """An existing note the search index ranked closest to a freshly created one."""

    title: str
    permalink: str | None
    file_path: str


def compose_similarity_probe(title: str, content: str) -> str:
    """Build the text used to look for existing notes near a freshly written one."""
    body = remove_frontmatter(content)
    return f"{title}\n\n{body}"[:SIMILAR_NOTES_PROBE_CHARS].strip()


def _wikilink_targets(content: str) -> list[str]:
    """Return a note's distinct wikilink targets, in the order they first appear."""
    return list(dict.fromkeys(match.strip() for match in _WIKILINK_TARGET.findall(content)))


def similar_notes_min_similarity(config: BasicMemoryConfig) -> float | None:
    """Return the advisory's similarity floor, or None to use the server's own.

    Cosine scores are model-specific: on OpenAI's text-embedding-3-small correct
    paraphrases cluster near 0.37, so the floor measured on the default model would hide
    real duplicates there. Only the exact embedding setup it was measured on gets the
    floor; anything else keeps semantic_min_similarity, which users tune for their model.
    The setup is read from this process's config, which describes the server a local
    write reaches; a write routed elsewhere with a different local setup falls back to
    that server's floor, the behavior before the advisory floor existed.
    """
    # Only FastEmbed can be the measured setup; checking it first also keeps the
    # identity resolution away from provider configs it would reject.
    if config.semantic_embedding_provider.strip().lower() != "fastembed":
        return None
    # The persisted embedding identity is what decides whether stored vectors are
    # reusable, so it is the definition of "the same embeddings": it covers the model,
    # dimensions, and document/query prefixes. model_construct() yields the field
    # defaults without reading BASIC_MEMORY_* overrides, which would otherwise make an
    # env-configured model compare equal to itself.
    measured = configured_embedding_provider_identity(BasicMemoryConfig.model_construct())
    if configured_embedding_provider_identity(config) != measured:
        return None
    # A user who raised the search floor above the advisory's keeps the stricter one.
    return max(config.semantic_min_similarity, SIMILAR_NOTES_MIN_SIMILARITY)


def similar_notes_query(title: str, content: str, *, min_similarity: float | None) -> SearchQuery:
    """Build the search that asks which existing notes might be the one just written.

    Vector-only retrieval keeps the probe out of the FTS query parser, which would read
    parentheses and boolean words in ordinary prose as operators. Notes that declare
    `generated:` in frontmatter (folder indexes, logs, run logs) are rewritten by their
    generator, so nobody means to write to them; they are left out of the candidates.
    """
    return SearchQuery(
        text=compose_similarity_probe(title, content),
        retrieval_mode=SearchRetrievalMode.VECTOR,
        entity_types=[SearchItemType.ENTITY],
        metadata_filters={"generated": None},
        min_similarity=min_similarity,
    )


def collapse_similar_notes(
    results: Sequence[SearchResult],
    *,
    exclude_file_path: str,
    exclude_permalink: str | None,
    linked_external_ids: frozenset[str],
    limit: int,
) -> list[SimilarNote]:
    """Drop the note that was just written and keep one row per remaining note.

    The new note's own row may or may not be indexed yet — vectors publish in the
    background — so it is removed when present rather than expected. Notes the new
    note already links to are dropped too: the caller has related them on purpose,
    which is the advisory's "related but distinct" answer given in advance.
    """
    similar_notes: list[SimilarNote] = []
    seen_paths: set[str] = set()
    for result in results:
        is_new_note = result.file_path == exclude_file_path or (
            exclude_permalink is not None and result.permalink == exclude_permalink
        )
        is_linked = result.external_id in linked_external_ids
        if is_new_note or is_linked or result.file_path in seen_paths:
            continue
        seen_paths.add(result.file_path)
        similar_notes.append(
            SimilarNote(title=result.title, permalink=result.permalink, file_path=result.file_path)
        )
        if len(similar_notes) == limit:
            break
    return similar_notes


async def find_similar_notes(
    search_client: SearchClient,
    knowledge_client: KnowledgeClient,
    *,
    title: str,
    content: str,
    exclude_file_path: str,
    exclude_permalink: str | None,
    min_similarity: float | None,
) -> list[SimilarNote]:
    """Ask the vector index which existing notes sit closest to the note just written."""
    query = similar_notes_query(title, content, min_similarity=min_similarity)
    # The cap keeps a hub note with hundreds of links from costing hundreds of requests;
    # a linked note past it can still be listed, which the advisory tolerates.
    linked_targets = _wikilink_targets(content)[:SIMILAR_NOTES_LINKED_ALLOWANCE]
    # Extra rows leave room for the new note's own hit and for the notes it links to,
    # which are dropped before collapsing.
    response = await search_client.search(
        query.model_dump(), page=1, page_size=SIMILAR_NOTES_LIMIT + 1 + len(linked_targets)
    )

    # Trigger: the search found neighbors and the new note has wikilinks.
    # Why: a neighbor the caller already linked is the advisory's "related but distinct"
    #   answer given in advance. Which note a link names is the link resolver's call
    #   (titles, permalinks, relative and rooted paths, workspace-qualified forms), so
    #   each link is resolved by the server from the new note's path, not re-derived here.
    # Outcome: neighbors whose external_id a link resolves to are dropped.
    linked_external_ids: frozenset[str] = frozenset()
    if response.results and linked_targets:
        resolved = await asyncio.gather(
            *(
                knowledge_client.resolve_link(target, source_path=exclude_file_path)
                for target in linked_targets
            )
        )
        linked_external_ids = frozenset(link.external_id for link in resolved if link)

    return collapse_similar_notes(
        response.results,
        exclude_file_path=exclude_file_path,
        exclude_permalink=exclude_permalink,
        linked_external_ids=linked_external_ids,
        limit=SIMILAR_NOTES_LIMIT,
    )


def probe_declined_by_server(error: BaseException) -> bool:
    """Return whether the search API refused the probe outright (HTTP 400).

    That is how the API answers when semantic search is disabled or has no embedding
    provider: a configuration state of the routed server, not a failure worth a warning
    on every create.
    """
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, HTTPStatusError):
            return current.response.status_code == 400
        current = current.__cause__
    return False


def format_similar_notes_section(
    similar_notes: Sequence[SimilarNote], *, same_topic: Callable[[str], str]
) -> str:
    """Render the advisory as a question with the ways out spelled out.

    Similarity scores are deliberately absent: on the default model a near-duplicate and
    a merely related note score in the same band, so a number would imply a precision the
    signal does not have. Ranking is the useful part.

    `same_topic` renders the "merge into the closest note" calls for the closest note's
    reference, in the calling tool's own vocabulary (write_note or bm_write).
    """
    closest = similar_notes[0]
    closest_ref = closest.permalink or closest.file_path
    lines = [
        "",
        "## Similar existing notes",
        "This note looks similar to notes that already exist. "
        "Did you mean to write to one of these?",
        *(f"- {note.title} (`{note.permalink or note.file_path}`)" for note in similar_notes),
        "",
        "| If it is... | Then |",
        "|---|---|",
        f"| The same topic | {same_topic(closest_ref)} |",
        f"| Related but distinct | Add a relation here, e.g. `- relates_to [[{closest.title}]]` |",
        "| Unrelated | Nothing to do |",
    ]
    return "\n".join(lines)
