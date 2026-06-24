"""Tests for the flat->structured corpus converter (no live LLM calls)."""

from __future__ import annotations

import pytest

from basic_memory_benchmarks.converters.structure_corpus import (
    _clean_extraction,
    _split_doc,
    group_prefix_filter,
    structure_corpus,
    structure_doc,
)
from basic_memory_benchmarks.llm.runners import LLMResult, LLMRunner

FLAT_DOC = """\
---
title: grp-c000
type: note
source_doc_id: grp-c000
dataset_id: convomem
permalink: bm-bench/grp-c000
---

# grp-c000

## Conversation
- **User:** We had a phishing attack that impersonated the CFO.
- **Assistant:** Email is a major vector for spoofing; use a secure portal.
"""

STRUCTURED_OUT = """\
## Observations
- [event] A phishing attack impersonated the CFO #security
- [risk] Email is a major vector for spoofing attacks

## Relations
- caused_by [[Phishing Incident]]
- mitigated_by [[Secure Internal Portal]]
"""


class _FakeRunner(LLMRunner):
    """Returns canned structured text; records the prompts it was given."""

    spec = "fake:extractor"

    def __init__(self, output: str = STRUCTURED_OUT) -> None:
        self.output = output
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult(
            text=self.output, model="fake", input_tokens=1, output_tokens=1, latency_ms=1.0
        )


def test_split_doc_separates_header_and_transcript():
    header, body = _split_doc(FLAT_DOC)
    assert "# grp-c000" in header
    assert "## Conversation" not in header
    assert "phishing attack" in body


def test_split_doc_rejects_doc_without_conversation():
    with pytest.raises(ValueError):
        _split_doc("---\ntitle: x\n---\n# x\n\n## Observations\n- [fact] hi\n")


def test_clean_extraction_strips_fences_and_preamble():
    fenced = "Here you go:\n```markdown\n## Observations\n- [fact] x\n```"
    assert _clean_extraction(fenced).startswith("## Observations")


def test_replace_mode_swaps_body_and_keeps_identity():
    out = structure_doc(FLAT_DOC, _FakeRunner(), mode="replace")
    # Identity-bearing frontmatter preserved so recall stays comparable.
    assert "source_doc_id: grp-c000" in out
    assert "permalink: bm-bench/grp-c000" in out
    assert "# grp-c000" in out
    # Body is BM-native, transcript gone.
    assert "## Observations" in out and "## Relations" in out
    assert "## Conversation" not in out


def test_augment_mode_keeps_transcript_and_appends_structure():
    runner = _FakeRunner()
    out = structure_doc(FLAT_DOC, runner, mode="augment")
    # Transcript retained AND structure appended.
    assert "## Conversation" in out
    assert "phishing attack that impersonated the CFO" in out
    assert "## Observations" in out and "## Relations" in out
    # The conversation text was handed to the extractor.
    assert "phishing attack" in runner.prompts[0]


def test_structure_doc_fails_loud_on_empty_extraction():
    with pytest.raises(ValueError):
        structure_doc(FLAT_DOC, _FakeRunner(output="I could not find anything."))


def _write(root, rel, doc_id):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FLAT_DOC.replace("grp-c000", doc_id), encoding="utf-8")


def test_structure_corpus_mirrors_flat_layout(tmp_path):
    src = tmp_path / "docs"
    _write(src, "locomo-c00-s01.md", "locomo-c00-s01")
    _write(src, "locomo-c00-s02.md", "locomo-c00-s02")

    out = tmp_path / "structured"
    n = structure_corpus(input_root=src, output_root=out, runner=_FakeRunner(), max_workers=2)

    assert n == 2
    assert sorted(p.name for p in (out).rglob("*.md")) == [
        "locomo-c00-s01.md",
        "locomo-c00-s02.md",
    ]
    assert "## Observations" in (out / "locomo-c00-s01.md").read_text()


def test_structure_corpus_grouped_layout_and_category_filter(tmp_path):
    groups = tmp_path / "groups"
    _write(groups, "implicit_connection-cs10-b1-0001/docs/c000.md", "g1-c000")
    _write(groups, "implicit_connection-cs10-b1-0001/docs/c001.md", "g1-c001")
    _write(groups, "preference-cs10-b2-0002/docs/c000.md", "g2-c000")  # other category

    out = tmp_path / "structured"
    n = structure_corpus(
        input_root=groups,
        output_root=out,
        runner=_FakeRunner(),
        path_filter=group_prefix_filter({"implicit_connection"}),
        max_workers=2,
    )

    assert n == 2  # preference group filtered out
    assert (out / "implicit_connection-cs10-b1-0001" / "docs" / "c000.md").exists()
    assert not (out / "preference-cs10-b2-0002").exists()
