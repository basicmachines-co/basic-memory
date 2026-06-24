"""Restructure a flat conversation corpus into Basic Memory's native form.

The default benchmark corpus stores each conversation as a flat
``## Conversation`` transcript, so Basic Memory is exercised purely as a
text-search engine over chat logs — none of its actual representation (typed
``- [category]`` observations and ``- relation [[Entity]]`` links) is used. That
can undersell a system whose strength is a structured knowledge graph,
especially where the answer requires joining facts the prose states separately.

This converter rewrites each conversation doc into the note shape a Basic Memory
user/agent would actually write. Two modes:

- ``augment`` (default, the faithful one): keep the original transcript and
  *append* an LLM-extracted ``## Observations`` + ``## Relations`` block. This is
  how Basic Memory really works — a note keeps its prose AND its parsed
  observations/relations become first-class searchable units — so it can only
  add signal, never lose the source text.
- ``replace``: swap the transcript for the extracted structure only. Useful to
  measure the pure distilled representation, but strictly lossy.

Document ids and frontmatter are always preserved, so retrieval ground truth and
recall stay directly comparable to the flat corpus, and every downstream stage —
provider ingest, retrieval scoring, QA, the failure diagnostic — works unchanged.
The extractor runs through the same plan-billed ``claude -p`` runner used
elsewhere, so there is no API spend. Output mirrors the input directory layout,
so both grouped (``groups/<g>/docs``) and flat (``docs``) corpora are supported.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Literal

from basic_memory_benchmarks.llm.runners import LLMRunner

Mode = Literal["augment", "replace"]

EXTRACTION_PROMPT = """\
You are converting one conversation into a Basic Memory structured note.

Basic Memory represents knowledge as typed observations and relations, NOT prose:
- An observation is a single fact written as: `- [category] the fact #optional-tag`
  Categories are short lowercase labels you choose to fit the fact, e.g.
  [fact], [event], [preference], [decision], [requirement], [risk], [recommendation].
- A relation links the note's subject to another entity:
  `- relation_type [[Entity Name]]`
  Use natural, consistent entity names (e.g. [[Email Communication]],
  [[Phishing Incident]]) and verb-like relation types (relates_to, caused_by,
  mitigated_by, prefers, concerns, depends_on).

Extract EVERY concrete, durable fact the conversation establishes — what was
said, decided, preferred, experienced, or recommended — as observations,
preserving specific names, dates, numbers, and personal details verbatim. Then
capture how the entities involved connect to each other as relations. Make
implicit connections explicit: if the conversation links two things (an incident
to a vulnerability, a preference to a constraint), write that as a relation or an
observation. Do not editorialize, summarize the chit-chat, or invent facts that
are not supported by the text.

Output ONLY markdown in exactly this form, nothing else (no preamble, no fences):

## Observations
- [category] ...
- [category] ...

## Relations
- relation_type [[Entity]]
- relation_type [[Entity]]

Conversation to convert:
{conversation}
"""

_CONVERSATION_RE = re.compile(r"\n## Conversation\b", re.DOTALL)


def _split_doc(flat_text: str) -> tuple[str, str]:
    """Return (header, conversation_body): header is everything up to (excluding)
    the ``## Conversation`` heading, body is the transcript after it."""
    match = _CONVERSATION_RE.search(flat_text)
    if not match:
        raise ValueError("Doc has no '## Conversation' section to restructure")
    header = flat_text[: match.start()].rstrip() + "\n"
    body = flat_text[match.end() :].strip()
    return header, body


def _clean_extraction(text: str) -> str:
    """Strip stray code fences / preamble and ensure it starts at Observations."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = re.sub(r"^[a-zA-Z]+\n", "", cleaned).strip()
    idx = cleaned.find("## Observations")
    if idx > 0:
        cleaned = cleaned[idx:]
    return cleaned.strip()


def structure_doc(flat_text: str, runner: LLMRunner, *, mode: Mode = "augment") -> str:
    """Convert one flat conversation doc into a BM-native note (doc id kept).

    ``augment`` retains the transcript and appends the structured block;
    ``replace`` substitutes the structured block for the transcript.
    """
    header, conversation = _split_doc(flat_text)
    result = runner.complete(EXTRACTION_PROMPT.format(conversation=conversation))
    structured = _clean_extraction(result.text)
    if "## Observations" not in structured:
        # Fail loud so a bad slice can't masquerade as a structured corpus.
        raise ValueError(f"Extractor returned no observations for a doc:\n{result.text[:400]}")
    if mode == "replace":
        return f"{header}\n{structured}\n"
    return f"{flat_text.rstrip()}\n\n{structured}\n"


def structure_corpus(
    *,
    input_root: Path,
    output_root: Path,
    runner: LLMRunner,
    mode: Mode = "augment",
    path_filter: Callable[[Path], bool] | None = None,
    max_workers: int = 4,
) -> int:
    """Restructure every ``*.md`` under ``input_root`` into ``output_root``,
    mirroring the relative directory layout. ``path_filter`` (given the source
    path) selects which docs to convert. Returns the doc count."""
    docs = [
        path
        for path in sorted(input_root.rglob("*.md"))
        if path_filter is None or path_filter(path)
    ]

    def _work(doc_path: Path) -> None:
        structured = structure_doc(doc_path.read_text(encoding="utf-8"), runner, mode=mode)
        dest = output_root / doc_path.relative_to(input_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(structured, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_work, docs))

    return len(docs)


def group_prefix_filter(categories: set[str]) -> Callable[[Path], bool]:
    """Path filter matching grouped corpora: keep docs whose group directory name
    starts with one of ``categories`` (e.g. ``implicit_connection-cs10-…``)."""

    def _filter(path: Path) -> bool:
        return any(part.startswith(f"{cat}-") for part in path.parts for cat in categories)

    return _filter
