"""Fixture <-> spec integrity: gold values are recomputed from the checked-in corpus.

Data-driven: if a corpus note or a task spec drifts, these tests name the exact
mismatch instead of letting a live run fail mysteriously.
"""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter

from basic_memory_benchmarks.agent_tasks.corpus import (
    RECENT_AGE_DAYS,
    TIMESTAMPS,
    corpus_checksum,
)
from basic_memory_benchmarks.agent_tasks.spec import (
    AnswerSetEquals,
    MarkerAbsent,
    MarkerPresent,
    RelationResolves,
)
from basic_memory_benchmarks.agent_tasks.tasks import (
    RECENT_TITLES,
    TASKS,
    TASKS_BY_ID,
    select_tasks,
)

CORPUS_DIR = Path(__file__).parents[2] / "benchmarks" / "datasets" / "agent-tasks" / "corpus"
WIKI_LINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
REQUIRED_SKILLS = {
    "memory-continue",
    "memory-curate",
    "memory-metadata-search",
    "memory-tasks",
    "manual",
}


def _corpus() -> dict[str, str]:
    assert CORPUS_DIR.is_dir(), f"corpus fixture missing: {CORPUS_DIR}"
    return {
        str(path.relative_to(CORPUS_DIR)): path.read_text(encoding="utf-8")
        for path in sorted(CORPUS_DIR.rglob("*.md"))
    }


def _metadata(text: str) -> dict:
    return dict(frontmatter.loads(text).metadata)


def _referenced_markers() -> set[str]:
    markers: set[str] = set()
    for task in TASKS:
        for grader in task.graders:
            if isinstance(grader, (MarkerPresent, MarkerAbsent)):
                markers.add(grader.marker)
    return markers


def test_task_ids_unique_and_at_least_ten() -> None:
    ids = [task.id for task in TASKS]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 10


def test_every_required_skill_area_is_covered() -> None:
    assert REQUIRED_SKILLS <= {task.skill for task in TASKS}


def test_select_tasks_rejects_unknown_ids() -> None:
    assert [task.id for task in select_tasks([])] == sorted(TASKS_BY_ID)
    try:
        select_tasks(["nope"])
    except ValueError as exc:
        assert "nope" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("unknown id accepted")


def test_every_referenced_marker_occurs_exactly_once_in_corpus() -> None:
    corpus = _corpus()
    all_text = "\n".join(corpus.values())
    for marker in sorted(_referenced_markers()):
        count = all_text.count(marker)
        assert count == 1, f"marker {marker} occurs {count} times in the corpus"


def test_task_prompts_never_leak_markers() -> None:
    for task in TASKS:
        assert "BMEVAL-" not in task.prompt or task.id == "man-chain", task.id
    # man-chain names only the prefix, never a full marker.
    assert re.search(r"BMEVAL-[a-z0-9-]+", TASKS_BY_ID["man-chain"].prompt) is None


def test_gold_permalinks_map_to_corpus_files() -> None:
    corpus = _corpus()
    for task in TASKS:
        for grader in task.graders:
            if isinstance(grader, AnswerSetEquals) and grader.key == "permalinks":
                for permalink in grader.gold:
                    assert f"{permalink}.md" in corpus, f"{task.id}: {permalink}"
            if isinstance(grader, RelationResolves):
                assert f"{grader.source_permalink}.md" in corpus
                for target in grader.targets:
                    assert f"{target}.md" in corpus


def test_orphan_gold_matches_link_scan() -> None:
    corpus = _corpus()
    title_to_relpath = {
        str(_metadata(text).get("title")): relpath for relpath, text in corpus.items()
    }
    outbound: dict[str, set[str]] = {}
    for relpath, text in corpus.items():
        body = frontmatter.loads(text).content
        outbound[relpath] = {
            title_to_relpath[target.strip()]
            for target in WIKI_LINK_PATTERN.findall(body)
            if target.strip() in title_to_relpath
        }
    inbound: dict[str, set[str]] = {relpath: set() for relpath in corpus}
    for source, targets in outbound.items():
        for target in targets:
            inbound[target].add(source)

    computed_orphans = {
        relpath.removesuffix(".md")
        for relpath in corpus
        if not outbound[relpath] and not inbound[relpath]
    }
    gold = next(
        grader.gold
        for grader in TASKS_BY_ID["curate-orphans"].graders
        if isinstance(grader, AnswerSetEquals)
    )
    assert computed_orphans == set(gold)


def _notes_metadata() -> dict[str, dict]:
    return {relpath.removesuffix(".md"): _metadata(text) for relpath, text in _corpus().items()}


def test_meta_status_priority_gold_recomputed() -> None:
    notes = _notes_metadata()
    computed = {
        permalink
        for permalink, meta in notes.items()
        if meta.get("status") == "active" and meta.get("priority") in ("high", "critical")
    }
    gold = next(
        grader.gold
        for grader in TASKS_BY_ID["meta-status-priority"].graders
        if isinstance(grader, AnswerSetEquals)
    )
    assert computed == set(gold)
    # The trap: a note whose BODY mentions "priority: high" but whose
    # frontmatter priority is low must stay excluded.
    trap = _corpus()["notes/postgres-vacuum-notes.md"]
    assert "priority: high" in frontmatter.loads(trap).content
    assert notes["notes/postgres-vacuum-notes"]["priority"] == "low"


def test_meta_confidence_gold_recomputed_with_boundary() -> None:
    notes = _notes_metadata()
    computed = {
        permalink
        for permalink, meta in notes.items()
        if isinstance(meta.get("confidence"), (int, float)) and meta["confidence"] > 0.7
    }
    gold = next(
        grader.gold
        for grader in TASKS_BY_ID["meta-confidence-gt"].graders
        if isinstance(grader, AnswerSetEquals)
    )
    assert computed == set(gold)
    # The $gt-vs-$gte boundary note sits exactly at 0.7 and is excluded.
    boundary = [meta for meta in notes.values() if meta.get("confidence") == 0.7]
    assert boundary, "corpus lost its 0.7 confidence boundary note"


def test_meta_nested_review_gold_recomputed() -> None:
    notes = _notes_metadata()
    computed = {
        permalink
        for permalink, meta in notes.items()
        if meta.get("status") == "draft"
        and isinstance(meta.get("review"), dict)
        and meta["review"].get("status") == "pending"
    }
    gold = next(
        grader.gold
        for grader in TASKS_BY_ID["meta-nested-review"].graders
        if isinstance(grader, AnswerSetEquals)
    )
    assert computed == set(gold)


def test_timestamps_cover_exactly_the_recent_gold_files() -> None:
    corpus = _corpus()
    for relpath, age in TIMESTAMPS.items():
        assert relpath in corpus, f"TIMESTAMPS references missing file {relpath}"
        assert age == RECENT_AGE_DAYS
    recent_titles = {str(_metadata(corpus[relpath]).get("title")) for relpath in TIMESTAMPS}
    assert recent_titles == set(RECENT_TITLES)


def test_corpus_checksum_is_stable_and_counts_files() -> None:
    checksum_a, count_a = corpus_checksum(CORPUS_DIR)
    checksum_b, count_b = corpus_checksum(CORPUS_DIR)
    assert checksum_a == checksum_b
    assert count_a == count_b == len(_corpus())
