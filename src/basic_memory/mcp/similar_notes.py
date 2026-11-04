"""Similar-note advisory: after a create, list the existing notes nearest to it.

Agents writing across sessions know the topic but not whether a note on it already
exists; the closest notes are surfaced and the decision stays with the caller.
"""

import asyncio
import dataclasses
import re
from collections.abc import Callable, Sequence

from httpx import HTTPStatusError

from basic_memory.file_utils import remove_frontmatter
from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.schemas.search import (
    SearchItemType,
    SearchQuery,
    SearchResult,
    SearchRetrievalMode,
    SimilarityFloor,
)

# How many neighbors to surface after a create. Ranking is reliable enough to put the note
# an agent probably meant at the top; a longer list adds noise, not signal.
SIMILAR_NOTES_LIMIT = 3
# The vector index embeds a note's title and opening content as its first chunk, so the
# probe copies that shape and length to land in the same neighborhood as the note itself.
SIMILAR_NOTES_PROBE_CHARS = 900
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


def similar_notes_query(title: str, content: str) -> SearchQuery:
    """Build the search that asks which existing notes might be the one just written.

    Vector-only retrieval keeps the probe out of the FTS query parser, which would read
    parentheses and boolean words in ordinary prose as operators. Notes that declare
    `generated:` in frontmatter (folder indexes, logs, run logs) are rewritten by their
    generator, so nobody means to write to them; they are left out of the candidates.
    The server applies the advisory's similarity floor, because only it knows which
    embedding model scored the candidates (#1982).
    """
    return SearchQuery(
        text=compose_similarity_probe(title, content),
        retrieval_mode=SearchRetrievalMode.VECTOR,
        entity_types=[SearchItemType.ENTITY],
        metadata_filters={"generated": None},
        similarity_floor=SimilarityFloor.SIMILAR_NOTES,
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
) -> list[SimilarNote]:
    """Ask the vector index which existing notes sit closest to the note just written."""
    query = similar_notes_query(title, content)
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


def _permalink_or_path(note: SimilarNote) -> str:
    return note.permalink or note.file_path


def format_similar_notes_section(
    similar_notes: Sequence[SimilarNote],
    *,
    same_topic: Callable[[str], str],
    reference: Callable[[SimilarNote], str] = _permalink_or_path,
) -> str:
    """Render the advisory as a question with the ways out spelled out.

    Similarity scores are deliberately absent: on the default model a near-duplicate and
    a merely related note score in the same band, so a number would imply a precision the
    signal does not have. Ranking is the useful part.

    `same_topic` renders the "merge into the closest note" calls for the closest note's
    reference, in the calling tool's own vocabulary; `reference` picks how a note is
    named, its permalink by default.
    """
    closest = similar_notes[0]
    closest_ref = reference(closest)
    lines = [
        "",
        "## Similar existing notes",
        "This note looks similar to notes that already exist. "
        "Did you mean to write to one of these?",
        *(f"- {note.title} (`{reference(note)}`)" for note in similar_notes),
        "",
        "| If it is... | Then |",
        "|---|---|",
        f"| The same topic | {same_topic(closest_ref)} |",
        f"| Related but distinct | Add a relation here, e.g. `- relates_to [[{closest.title}]]` |",
        "| Unrelated | Nothing to do |",
    ]
    return "\n".join(lines)
