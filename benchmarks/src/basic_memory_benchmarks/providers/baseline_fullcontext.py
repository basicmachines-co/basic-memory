"""Full-context baseline provider.

The other honesty floor: skip retrieval entirely and hand the answering model
the whole corpus. On corpora that fit a modern context window (LoCoMo
conversations, LongMemEval-S groups), published results repeatedly show
full-context beating dedicated memory systems — so a memory system's QA
accuracy should be read against this number, and its token cost against this
provider's token cost.

Retrieval metrics (recall/MRR) are meaningless for this provider by design:
it returns the corpus as a single hit with no doc-id claims. Use it for the
QA stage only.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter

from basic_memory_benchmarks.models import RunConfig, SearchHit
from basic_memory_benchmarks.providers.base import BenchmarkProvider

# Cap the assembled context so a pathological corpus cannot blow past the
# answering model's window. ~600K chars ≈ 150K tokens, inside a 200K window
# with room for prompt scaffolding. LongMemEval-S groups (~115K tokens) fit.
MAX_CONTEXT_CHARS = 600_000


class FullContextProvider(BenchmarkProvider):
    """No-retrieval baseline: the whole corpus is the context."""

    name = "baseline-fullcontext"

    def __init__(self) -> None:
        self._context: str = ""
        self._truncated: bool = False

    def ingest(self, corpus_path: Path, run_config: RunConfig) -> None:
        _ = run_config
        sections: list[str] = []
        for note_path in sorted(corpus_path.rglob("*.md")):
            with note_path.open("r", encoding="utf-8") as handle:
                parsed = frontmatter.load(handle)
            sections.append(parsed.content.strip())
        context = "\n\n---\n\n".join(sections)
        self._truncated = len(context) > MAX_CONTEXT_CHARS
        self._context = context[:MAX_CONTEXT_CHARS]

    def search(self, query: str, limit: int, run_config: RunConfig) -> list[SearchHit]:
        _ = (query, limit, run_config)
        if not self._context:
            return []
        return [
            SearchHit(
                id="full-context",
                source_doc_id=None,
                source_path=None,
                text=self._context,
                score=1.0,
                metadata={"provider": self.name, "truncated": self._truncated},
            )
        ]

    def cleanup(self, run_config: RunConfig) -> None:
        _ = run_config
        self._context = ""
        self._truncated = False

    def version_info(self) -> dict[str, str]:
        return {
            "baseline": "full-context",
            "max_context_chars": str(MAX_CONTEXT_CHARS),
        }
