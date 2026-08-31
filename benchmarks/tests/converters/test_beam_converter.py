"""Tests for the BEAM grouped corpus converter (transcript -> notes + queries)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basic_memory_benchmarks.converters.beam_to_corpus import convert_beam_to_corpus
from basic_memory_benchmarks.datasets.beam import ABILITY_KEYS
from basic_memory_benchmarks.models import QueryCase
from basic_memory_benchmarks.utils import sha256_file
from beam_fixture import (
    ABSTENTION_IDEAL_RESPONSE,
    conversation_two_full_chat,
    message,
    minimal_probes,
    write_beam_tier,
    write_conversation,
)


@pytest.fixture
def chats_root(tmp_path: Path) -> Path:
    return write_beam_tier(tmp_path / "chats")


def _convert(chats_root: Path, output_dir: Path, **kwargs) -> tuple[Path, Path, int, int]:
    return convert_beam_to_corpus(
        dataset_root=chats_root, output_dir=output_dir, tier="100K", **kwargs
    )


def _queries_by_id(queries_path: Path) -> dict[str, dict]:
    return {query["id"]: query for query in json.loads(queries_path.read_text())}


class TestGroupedLayout:
    def test_groups_docs_and_counts(self, chats_root: Path, tmp_path: Path) -> None:
        groups_dir, queries_path, doc_count, query_count = _convert(chats_root, tmp_path / "out")

        # Conversation 1: batch 1 has two sessions, batch 2 one; conv 2: one.
        assert doc_count == 4
        # Ten abilities x one probe x two conversations.
        assert query_count == 20
        assert (groups_dir / "beam-100k-c01" / "docs").is_dir()
        assert (groups_dir / "beam-100k-c02" / "docs").is_dir()

        doc_paths = sorted((groups_dir / "beam-100k-c01" / "docs").glob("*.md"))
        assert [path.stem for path in doc_paths] == [
            "beam-100k-c01-b01-s000",
            "beam-100k-c01-b01-s001",
            "beam-100k-c01-b02-s000",
        ]

    def test_filename_matches_source_doc_id(self, chats_root: Path, tmp_path: Path) -> None:
        groups_dir, _, _, _ = _convert(chats_root, tmp_path / "out")

        for path in groups_dir.rglob("*.md"):
            assert f"source_doc_id: {path.stem}" in path.read_text()

    def test_max_conversations(self, chats_root: Path, tmp_path: Path) -> None:
        groups_dir, _, doc_count, query_count = _convert(
            chats_root, tmp_path / "out", max_conversations=1
        )

        assert doc_count == 3
        assert query_count == 10
        assert not (groups_dir / "beam-100k-c02").exists()

    def test_unsupported_mode_raises(self, chats_root: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="ingestion mode"):
            _convert(chats_root, tmp_path / "out", mode="curated")


class TestTranscriptRendering:
    def test_frontmatter_and_anchor_carry(self, chats_root: Path, tmp_path: Path) -> None:
        groups_dir, _, _, _ = _convert(chats_root, tmp_path / "out")
        docs = groups_dir / "beam-100k-c01" / "docs"

        batch_one = (docs / "beam-100k-c01-b01-s000.md").read_text()
        assert "title: beam-100k-c01-b01-s000 (March-15-2024)" in batch_one
        assert "session_date: March-15-2024" in batch_one
        assert "# Chat session (March-15-2024)" in batch_one
        assert "- **User:** I adopted a puppy named Biscuit." in batch_one
        assert "- **Assistant:** Congratulations on adopting Biscuit!" in batch_one

        # Batch 2 has no batch-level anchor; the message-level anchor wins.
        batch_two = (docs / "beam-100k-c01-b02-s000.md").read_text()
        assert "session_date: April-02-2024" in batch_two
        assert "# Chat session (April-02-2024)" in batch_two

    def test_doc_without_anchor_uses_plain_heading(self, chats_root: Path, tmp_path: Path) -> None:
        groups_dir, _, _, _ = _convert(chats_root, tmp_path / "out")
        doc = (groups_dir / "beam-100k-c02" / "docs" / "beam-100k-c02-b01-s000.md").read_text()

        assert "title: beam-100k-c02-b01-s000\n" in doc
        assert "session_date:" not in doc
        assert "# beam-100k-c02-b01-s000" in doc

    def test_truncated_chat_variant_is_rendered(self, chats_root: Path, tmp_path: Path) -> None:
        groups_dir, _, _, _ = _convert(chats_root, tmp_path / "out")
        doc = (groups_dir / "beam-100k-c02" / "docs" / "beam-100k-c02-b01-s000.md").read_text()

        assert "TRUNCATED VERSION" in doc
        assert "FULL VERSION" not in doc

    def test_index_marker_stripped(self, chats_root: Path, tmp_path: Path) -> None:
        groups_dir, _, _, _ = _convert(chats_root, tmp_path / "out")

        corpus_text = "".join(path.read_text() for path in groups_dir.rglob("*.md"))
        assert "->->" not in corpus_text
        assert "- **User:** My dentist appointment is on March 29." in corpus_text
        # The non-numeric "->-> 2,N/A" variant is stripped too.
        assert "- **Assistant:** Noted: the dentist appointment is March 29." in corpus_text
        # The multi-id "->-> 2,22, 24" variant (space after a comma) as well.
        assert "- **User:** Update: my salary is now $75,000." in corpus_text
        # And the paren-suffixed "->-> 1,5)" variant: the ")" is generator
        # junk (no matching open paren upstream) and strips with the marker.
        assert "- **Assistant:** Got it, salary updated." in corpus_text
        assert "salary updated. )" not in corpus_text

    def test_surviving_marker_variant_fails_fast(self, tmp_path: Path) -> None:
        # A non-trailing marker escapes the strip pattern; conversion must
        # abort rather than leak probe indices into the rendered docs.
        chat = [
            {
                "batch_number": 1,
                "time_anchor": None,
                "turns": [
                    [
                        message("user", 0, "As I said ->-> 3,7 earlier, the meeting moved."),
                        message("assistant", 1, "Understood."),
                    ]
                ],
            }
        ]
        write_conversation(tmp_path / "chats" / "100K" / "1", chat, minimal_probes())

        with pytest.raises(ValueError, match="marker survived cleaning"):
            _convert(tmp_path / "chats", tmp_path / "out")

    def test_rubric_never_ingested(self, chats_root: Path, tmp_path: Path) -> None:
        groups_dir, queries_path, _, _ = _convert(chats_root, tmp_path / "out")

        corpus_text = "".join(path.read_text() for path in groups_dir.rglob("*.md"))
        for query in json.loads(queries_path.read_text()):
            for nugget in query["metadata"]["rubric"]:
                assert nugget not in corpus_text


class TestProbeToQueryMapping:
    def test_queries_validate_as_query_cases(self, chats_root: Path, tmp_path: Path) -> None:
        _, queries_path, _, _ = _convert(chats_root, tmp_path / "out")

        for raw in json.loads(queries_path.read_text()):
            case = QueryCase.model_validate(raw)
            assert case.group in {"beam-100k-c01", "beam-100k-c02"}
            assert case.category == case.metadata["ability"]
            assert case.metadata["dataset_id"] == "beam-100k"
            assert case.metadata["tier"] == "100K"

    def test_probe_identity_and_fields(self, chats_root: Path, tmp_path: Path) -> None:
        _, queries_path, _, _ = _convert(chats_root, tmp_path / "out")
        queries = _queries_by_id(queries_path)

        extraction = queries["beam-100k-c01-information_extraction-0"]
        assert extraction["query"] == "When is the dentist appointment?"
        assert extraction["category"] == "information_extraction"
        assert extraction["expected_answer"] == "March 29"
        assert extraction["metadata"]["probe_index"] == 0
        assert extraction["metadata"]["rubric"] == ["LLM response should state: March 29"]
        assert extraction["metadata"]["abstention"] is False

    def test_ground_truth_maps_message_ids_to_session_docs(
        self, chats_root: Path, tmp_path: Path
    ) -> None:
        _, queries_path, _, _ = _convert(chats_root, tmp_path / "out")
        queries = _queries_by_id(queries_path)

        # Message id 2 lives in batch 1, session 1.
        assert queries["beam-100k-c01-information_extraction-0"]["ground_truth"] == [
            "beam-100k-c01-b01-s001"
        ]
        # knowledge_update's dict ids (1 and 4) span two sessions.
        assert queries["beam-100k-c01-knowledge_update-0"]["ground_truth"] == [
            "beam-100k-c01-b01-s000",
            "beam-100k-c01-b02-s000",
        ]
        assert queries["beam-100k-c01-event_ordering-0"]["ground_truth"] == [
            "beam-100k-c01-b01-s000",
            "beam-100k-c01-b01-s001",
            "beam-100k-c01-b02-s000",
        ]

    def test_abstention_query_convention(self, chats_root: Path, tmp_path: Path) -> None:
        _, queries_path, _, _ = _convert(chats_root, tmp_path / "out")
        queries = _queries_by_id(queries_path)

        abstention = queries["beam-100k-c01-abstention-0"]
        assert abstention["ground_truth"] == []
        assert abstention["metadata"]["abstention"] is True
        assert abstention["expected_answer"] == ABSTENTION_IDEAL_RESPONSE

    def test_all_abilities_covered_per_conversation(self, chats_root: Path, tmp_path: Path) -> None:
        _, queries_path, _, _ = _convert(chats_root, tmp_path / "out")

        queries = json.loads(queries_path.read_text())
        for group in ("beam-100k-c01", "beam-100k-c02"):
            categories = {q["category"] for q in queries if q["group"] == group}
            assert categories == set(ABILITY_KEYS)

    def test_cited_id_missing_from_chat_raises(self, tmp_path: Path) -> None:
        probes = minimal_probes(source_chat_ids=[99])
        write_conversation(tmp_path / "chats" / "100K" / "1", conversation_two_full_chat(), probes)

        with pytest.raises(ValueError, match="cites chat id 99"):
            _convert(tmp_path / "chats", tmp_path / "out")


class TestConversionManifest:
    def test_manifest_records_provenance(self, chats_root: Path, tmp_path: Path) -> None:
        _convert(chats_root, tmp_path / "out")
        manifest = json.loads((tmp_path / "out" / "conversion.json").read_text())

        assert manifest["dataset_id"] == "beam-100k"
        assert manifest["tier"] == "100K"
        assert manifest["converter"] == {"mode": "raw", "max_conversations": None}

        by_conv = {entry["conversation_id"]: entry for entry in manifest["conversations"]}
        assert by_conv["1"]["chat_file"] == "chat.json"
        assert by_conv["2"]["chat_file"] == "chat_trunecated.json"
        # Checksums pin the exact files consumed (conv 2: the truncated one).
        conv_two = chats_root / "100K" / "2"
        assert by_conv["2"]["chat_sha256"] == sha256_file(conv_two / "chat_trunecated.json")
        assert by_conv["2"]["probing_sha256"] == sha256_file(
            conv_two / "probing_questions" / "probing_questions.json"
        )

    def test_conversion_is_deterministic(self, chats_root: Path, tmp_path: Path) -> None:
        groups_one, queries_one, _, _ = _convert(chats_root, tmp_path / "out1")
        groups_two, queries_two, _, _ = _convert(chats_root, tmp_path / "out2")

        assert queries_one.read_text() == queries_two.read_text()
        docs_one = sorted(groups_one.rglob("*.md"))
        docs_two = sorted(groups_two.rglob("*.md"))
        assert [path.relative_to(groups_one) for path in docs_one] == [
            path.relative_to(groups_two) for path in docs_two
        ]
        for path_one, path_two in zip(docs_one, docs_two, strict=True):
            assert path_one.read_text() == path_two.read_text()
