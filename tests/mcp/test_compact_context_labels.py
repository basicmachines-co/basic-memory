"""Content-derived observation labels must not reintroduce omitted prose."""

from datetime import UTC, datetime

from basic_memory.mcp.tools.build_context import _compact_context_labels, _format_context_markdown
from basic_memory.schemas.memory import (
    ContextResult,
    GraphContext,
    MemoryMetadata,
    ObservationSummary,
)


def test_compact_observation_navigation_uses_owning_file_without_mutating_graph():
    observation = ObservationSummary(
        observation_id=12,
        entity_external_id="source-uuid",
        title="fact: Private source prose",
        file_path="notes/Source.md",
        permalink="notes/source/observations/fact/private-source-prose",
        category="fact",
        content="Private source prose",
        created_at=datetime.now(UTC),
    )
    graph = GraphContext(
        results=[ContextResult(primary_result=observation, related_results=[observation])],
        metadata=MemoryMetadata(depth=1),
    )
    compact = _compact_context_labels(graph)
    for item in [compact.results[0].primary_result, *compact.results[0].related_results]:
        assert isinstance(item, ObservationSummary)
        assert item.title == "fact"
        assert item.permalink == "notes/Source.md"
        assert item.entity_external_id == "source-uuid"
        assert item.observation_id == 12
    text = _format_context_markdown(compact, "project", compact=True)
    assert "notes/Source.md" in text
    assert "private" not in text.lower()
    assert graph.results[0].primary_result.title == "fact: Private source prose"
