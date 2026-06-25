"""Filesystem grep baseline provider.

The honesty floor for retrieval: case-insensitive term matching over the raw
corpus markdown with TF-style scoring and no index, no embeddings, no LLM.
A memory system that can't beat this isn't adding retrieval value. (Letta's
"filesystem + grep agent" scored 74% on LoCoMo; this is the non-agentic
deterministic analogue.)
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import frontmatter

from basic_memory_benchmarks.models import RunConfig, SearchHit
from basic_memory_benchmarks.providers.base import BenchmarkProvider

_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9'\-]*")

# Minimal English stopword set so query scoring keys on content-bearing terms.
_STOPWORDS = frozenset(
    "a an and are as at be but by did do does for from had has have how i in is it of on or "
    "that the their they this to was we were what when where which who whom whose why will "
    "with you your".split()
)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.lower())


class FilesystemGrepProvider(BenchmarkProvider):
    """Deterministic grep-style ranking over corpus files."""

    name = "baseline-grep"

    def __init__(self) -> None:
        self._docs: list[tuple[str, str, str]] = []  # (doc_id, rel_path, body)

    def ingest(self, corpus_path: Path, run_config: RunConfig) -> None:
        _ = run_config
        self._docs = []
        for note_path in sorted(corpus_path.rglob("*.md")):
            with note_path.open("r", encoding="utf-8") as handle:
                parsed = frontmatter.load(handle)
            doc_id = str(parsed.get("source_doc_id") or note_path.stem)
            rel_path = note_path.relative_to(corpus_path).as_posix()
            self._docs.append((doc_id, rel_path, parsed.content))

    def search(self, query: str, limit: int, run_config: RunConfig) -> list[SearchHit]:
        _ = run_config
        terms = [t for t in _tokenize(query) if t not in _STOPWORDS]
        if not terms:
            return []

        scored: list[tuple[float, str, str, str]] = []
        for doc_id, rel_path, body in self._docs:
            body_lower = body.lower()
            # TF with log damping per term; coverage bonus rewards docs
            # matching more distinct query terms over many hits of one term.
            matched = 0
            score = 0.0
            for term in terms:
                count = body_lower.count(term)
                if count:
                    matched += 1
                    score += 1.0 + math.log(count)
            if matched:
                # Squared coverage: a doc matching every distinct query term
                # must outrank a doc spamming one term many times.
                score *= (matched / len(terms)) ** 2
                scored.append((score, doc_id, rel_path, body))

        scored.sort(key=lambda item: item[0], reverse=True)
        hits: list[SearchHit] = []
        for score, doc_id, rel_path, body in scored[:limit]:
            hits.append(
                SearchHit(
                    id=doc_id,
                    source_doc_id=doc_id,
                    source_path=rel_path,
                    text=body[:2000],
                    score=score,
                    metadata={"provider": self.name},
                )
            )
        return hits

    def cleanup(self, run_config: RunConfig) -> None:
        _ = run_config
        self._docs = []

    def version_info(self) -> dict[str, str]:
        return {"baseline": "filesystem-grep", "index": "none"}
