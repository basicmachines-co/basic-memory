"""The similar-note advisory's similarity floor, owned by the server that searches.

Cosine scores are specific to the embedding model, so the floor is decided where the
model is known: the search service of the server holding the vectors. A local client
routed to Cloud and Cloud's in-process MCP tools then get the same answer (#1982).
"""

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
)

# The advisory asks "might this be the same note?", a stricter question than search's
# "is this relevant at all?" (semantic_min_similarity, 0.55). Measured on the default
# bge-small-en-v1.5 model (#1718): rewrites of an existing note scored 0.77-0.93 against
# it (0.78-0.87 on the Moby Dick vault, #1259), while the nearest neighbor of a note on an
# unrelated topic topped out at 0.67. Below this floor the list was only noise; near the
# top, a duplicate and a closely related note still share one band, so ranking decides.
# The number holds for that model only; see similar_notes_min_similarity.
SIMILAR_NOTES_MIN_SIMILARITY = 0.70


def similar_notes_min_similarity(config: BasicMemoryConfig) -> float | None:
    """Return the advisory's similarity floor for this server, or None for its search floor.

    Cosine scores are model-specific: on OpenAI's text-embedding-3-small correct
    paraphrases cluster near 0.37, so the floor measured on the default model would hide
    real duplicates there. Only the exact embedding setup it was measured on gets the
    floor; anything else keeps semantic_min_similarity, which users tune for their model.
    The setup is this server's config, the one that embedded the candidates.
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
