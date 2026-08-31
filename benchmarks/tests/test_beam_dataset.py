"""Tests for the BEAM dataset adapter (loading + tier filtering)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basic_memory_benchmarks.datasets.beam import (
    ABILITY_KEYS,
    load_beam_conversation,
    load_beam_tier,
)
from beam_fixture import (
    ABSTENTION_IDEAL_RESPONSE,
    conversation_two_full_chat,
    minimal_probes,
    probe,
    write_beam_tier,
    write_conversation,
)


@pytest.fixture
def chats_root(tmp_path: Path) -> Path:
    return write_beam_tier(tmp_path / "chats")


class TestLoadConversation:
    def test_batches_and_messages(self, chats_root: Path) -> None:
        conv = load_beam_conversation(chats_root / "100K" / "1", "100K")

        assert conv.conversation_id == "1"
        assert conv.tier == "100K"
        assert conv.source_chat_file == "chat.json"
        assert [batch.batch_number for batch in conv.batches] == [1, 2]
        assert conv.batches[0].time_anchor == "March-15-2024"
        assert conv.batches[1].time_anchor is None
        assert len(conv.batches[0].turns) == 2

        first_session = conv.batches[0].turns[0]
        assert [message.id for message in first_session] == [0, 1]
        assert [message.role for message in first_session] == ["user", "assistant"]

        marker_message = conv.batches[0].turns[1][0]
        assert marker_message.question_type == "main_question"
        assert marker_message.index == "1,2"

        anchored_message = conv.batches[1].turns[0][0]
        assert anchored_message.time_anchor == "April-02-2024"

    def test_prefers_truncated_chat(self, chats_root: Path) -> None:
        conv = load_beam_conversation(chats_root / "100K" / "2", "100K")

        assert conv.source_chat_file == "chat_trunecated.json"
        assert "TRUNCATED VERSION" in conv.batches[0].turns[0][0].content

    def test_probes_flattened_ability_major(self, chats_root: Path) -> None:
        conv = load_beam_conversation(chats_root / "100K" / "1", "100K")

        # One probe per ability in the fixture, so the flattened order is
        # exactly the upstream ability-key order.
        assert [item.ability for item in conv.probes] == list(ABILITY_KEYS)
        assert all(item.index == 0 for item in conv.probes)

    def test_probe_index_enumerates_within_ability(self, tmp_path: Path) -> None:
        probes = minimal_probes()
        probes["information_extraction"] = [
            probe(
                "information_extraction",
                "First probe?",
                rubric=["first nugget"],
                reference_answer="first",
                source_chat_ids=[0],
            ),
            probe(
                "information_extraction",
                "Second probe?",
                rubric=["second nugget"],
                reference_answer="second",
                source_chat_ids=[0],
            ),
        ]
        conv_dir = write_conversation(tmp_path / "1", conversation_two_full_chat(), probes)

        conv = load_beam_conversation(conv_dir, "100K")

        extraction = [item for item in conv.probes if item.ability == "information_extraction"]
        assert [(item.index, item.question) for item in extraction] == [
            (0, "First probe?"),
            (1, "Second probe?"),
        ]

    def test_reference_answer_field_resolution(self, chats_root: Path) -> None:
        conv = load_beam_conversation(chats_root / "100K" / "1", "100K")
        by_ability = {item.ability: item for item in conv.probes}

        # answer / ideal_response / ideal_summary fields all resolve.
        assert by_ability["information_extraction"].reference_answer == "March 29"
        assert by_ability["abstention"].reference_answer == ABSTENTION_IDEAL_RESPONSE
        assert by_ability["summarization"].reference_answer == "summarization reference"

    def test_source_chat_ids_normalization(self, chats_root: Path) -> None:
        conv = load_beam_conversation(chats_root / "100K" / "1", "100K")
        by_ability = {item.ability: item for item in conv.probes}

        # knowledge_update's dict shape flattens to the sorted union.
        assert by_ability["knowledge_update"].source_chat_ids == [1, 4]
        # abstention omits the field entirely.
        assert by_ability["abstention"].source_chat_ids == []
        # event_ordering mixes ints with int-list groups (live 100K shape).
        assert by_ability["event_ordering"].source_chat_ids == [0, 2, 4]

    def test_extras_passthrough(self, chats_root: Path) -> None:
        conv = load_beam_conversation(chats_root / "100K" / "1", "100K")
        by_ability = {item.ability: item for item in conv.probes}

        assert by_ability["event_ordering"].extras == {"ordering_type": "full"}
        assert by_ability["abstention"].extras == {"abstention_type": "never_mentioned"}
        # Consumed fields never leak into extras.
        assert "question" not in by_ability["event_ordering"].extras
        assert "rubric" not in by_ability["event_ordering"].extras


class TestLoadTier:
    def test_loads_conversations_in_numeric_order(self, tmp_path: Path) -> None:
        root = tmp_path / "chats"
        write_conversation(root / "100K" / "2", conversation_two_full_chat(), minimal_probes())
        write_conversation(root / "100K" / "10", conversation_two_full_chat(), minimal_probes())

        conversations = load_beam_tier(root, "100K")

        # Numeric ordering, not lexical ("10" would sort before "2" lexically).
        assert [conv.conversation_id for conv in conversations] == ["2", "10"]

    def test_tier_filtering_selects_tier_directory(self, chats_root: Path) -> None:
        write_conversation(
            chats_root / "500K" / "7", conversation_two_full_chat(), minimal_probes()
        )

        conversations = load_beam_tier(chats_root, "500K")

        assert [conv.conversation_id for conv in conversations] == ["7"]
        assert conversations[0].tier == "500K"

    def test_max_conversations(self, chats_root: Path) -> None:
        conversations = load_beam_tier(chats_root, "100K", max_conversations=1)
        assert [conv.conversation_id for conv in conversations] == ["1"]

    def test_unknown_tier_raises(self, chats_root: Path) -> None:
        with pytest.raises(ValueError, match="Unknown BEAM tier"):
            load_beam_tier(chats_root, "2M")

    def test_10m_tier_rejected(self, chats_root: Path) -> None:
        with pytest.raises(ValueError, match="plan-N"):
            load_beam_tier(chats_root, "10M")

    def test_missing_tier_directory_raises(self, chats_root: Path) -> None:
        with pytest.raises(FileNotFoundError, match="tier directory"):
            load_beam_tier(chats_root, "1M")

    def test_tier_without_numeric_dirs_raises(self, tmp_path: Path) -> None:
        (tmp_path / "chats" / "100K" / "not-a-number").mkdir(parents=True)
        with pytest.raises(ValueError, match="no numeric conversation dirs"):
            load_beam_tier(tmp_path / "chats", "100K")


class TestFailFast:
    def test_missing_ability_key_raises(self, tmp_path: Path) -> None:
        probes = minimal_probes()
        del probes["summarization"]
        conv_dir = write_conversation(tmp_path / "1", conversation_two_full_chat(), probes)

        with pytest.raises(ValueError, match="missing abilities"):
            load_beam_conversation(conv_dir, "100K")

    def test_non_int_in_chat_id_group_raises(self, tmp_path: Path) -> None:
        probes = minimal_probes()
        probes["information_extraction"][0]["source_chat_ids"] = [0, ["not-an-int"]]
        conv_dir = write_conversation(tmp_path / "1", conversation_two_full_chat(), probes)

        with pytest.raises(ValueError, match="must contain ints"):
            load_beam_conversation(conv_dir, "100K")

    def test_empty_rubric_raises(self, tmp_path: Path) -> None:
        probes = minimal_probes()
        probes["information_extraction"][0]["rubric"] = []
        conv_dir = write_conversation(tmp_path / "1", conversation_two_full_chat(), probes)

        with pytest.raises(ValueError, match="no rubric"):
            load_beam_conversation(conv_dir, "100K")

    def test_missing_reference_answer_field_raises(self, tmp_path: Path) -> None:
        probes = minimal_probes()
        del probes["information_extraction"][0]["answer"]
        conv_dir = write_conversation(tmp_path / "1", conversation_two_full_chat(), probes)

        with pytest.raises(ValueError, match="missing reference answer field"):
            load_beam_conversation(conv_dir, "100K")

    def test_plan_layout_chat_raises(self, tmp_path: Path) -> None:
        conv_dir = write_conversation(
            tmp_path / "1", conversation_two_full_chat(), minimal_probes()
        )
        (conv_dir / "chat.json").write_text(json.dumps([{"plan-1": []}]), encoding="utf-8")

        with pytest.raises(ValueError, match="plan-N"):
            load_beam_conversation(conv_dir, "100K")

    def test_empty_chat_raises(self, tmp_path: Path) -> None:
        conv_dir = write_conversation(
            tmp_path / "1", conversation_two_full_chat(), minimal_probes()
        )
        (conv_dir / "chat.json").write_text("[]", encoding="utf-8")

        with pytest.raises(ValueError, match="non-empty list of batches"):
            load_beam_conversation(conv_dir, "100K")

    def test_missing_chat_file_raises(self, tmp_path: Path) -> None:
        conv_dir = write_conversation(
            tmp_path / "1", conversation_two_full_chat(), minimal_probes()
        )
        (conv_dir / "chat.json").unlink()

        with pytest.raises(FileNotFoundError, match="no chat file"):
            load_beam_conversation(conv_dir, "100K")

    def test_missing_probing_questions_raises(self, tmp_path: Path) -> None:
        conv_dir = write_conversation(
            tmp_path / "1", conversation_two_full_chat(), minimal_probes()
        )
        (conv_dir / "probing_questions" / "probing_questions.json").unlink()

        with pytest.raises(FileNotFoundError, match="no probing questions"):
            load_beam_conversation(conv_dir, "100K")
