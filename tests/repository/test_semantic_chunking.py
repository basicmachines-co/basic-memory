"""Tests for pure semantic chunk planning."""

from types import SimpleNamespace

from basic_memory.repository import semantic_chunking
from basic_memory.repository.semantic_chunking import (
    MAX_VECTOR_CHUNK_CHARS,
    VectorChunkRecord,
    build_entity_fingerprint,
    build_vector_chunk_records,
    compose_row_source_text,
    split_text_into_chunks,
)
from basic_memory.schemas.search import SearchItemType


def _make_row(
    *,
    row_type: str,
    title: str = "Test Title",
    permalink: str = "test/permalink",
    content_stems: str = "",
    content_snippet: str = "",
    category: str = "",
    relation_type: str = "",
    row_id: int = 1,
):
    """Create a SimpleNamespace matching the semantic source-row contract."""
    return SimpleNamespace(
        id=row_id,
        type=row_type,
        title=title,
        permalink=permalink,
        content_stems=content_stems,
        content_snippet=content_snippet,
        category=category,
        relation_type=relation_type,
    )


class TestComposeRowSourceText:
    """Verify source text uses the human-readable fields for each row type."""

    def test_entity_row_uses_content_snippet_not_content_stems(self):
        row = _make_row(
            row_type=SearchItemType.ENTITY.value,
            title="Auth Design",
            permalink="specs/auth-design",
            content_stems="auth login token session stems expanded variants",
            content_snippet="JWT authentication with session management",
        )

        result = compose_row_source_text(row)

        assert "Auth Design" in result
        assert "specs/auth-design" in result
        assert "JWT authentication with session management" in result
        assert "stems expanded variants" not in result

    def test_observation_row_includes_category(self):
        row = _make_row(
            row_type=SearchItemType.OBSERVATION.value,
            title="Coffee Notes",
            permalink="notes/coffee",
            category="technique",
            content_snippet="Pour over produces cleaner cups",
        )

        result = compose_row_source_text(row)

        assert "Coffee Notes" in result
        assert "technique" in result
        assert "Pour over produces cleaner cups" in result

    def test_relation_row_includes_relation_type(self):
        row = _make_row(
            row_type=SearchItemType.RELATION.value,
            title="Brewing",
            permalink="notes/brewing",
            relation_type="relates_to",
            content_snippet="Coffee brewing method",
        )

        result = compose_row_source_text(row)

        assert "Brewing" in result
        assert "relates_to" in result
        assert "Coffee brewing method" in result

    def test_entity_row_with_none_fields(self):
        row = _make_row(
            row_type=SearchItemType.ENTITY.value,
            title="Minimal",
            permalink="",
            content_snippet="",
        )
        row.permalink = None
        row.content_snippet = None

        assert compose_row_source_text(row) == "Minimal"


class TestSplitTextIntoChunks:
    """Verify Markdown-aware text splitting."""

    def test_short_text_returns_single_chunk(self):
        assert split_text_into_chunks("Short text") == ["Short text"]

    def test_empty_text_returns_empty(self):
        assert split_text_into_chunks("") == []
        assert split_text_into_chunks("   ") == []

    def test_splits_on_headers(self):
        long_section_a = "## Section A\n" + ("A content. " * 100)
        long_section_b = "## Section B\n" + ("B content. " * 100)
        long_text = f"Intro paragraph\n\n{long_section_a}\n\n{long_section_b}"

        result = split_text_into_chunks(long_text)

        assert len(result) >= 2

    def test_paragraph_merging_within_limit(self):
        para1 = "First paragraph."
        para2 = "Second paragraph."
        text = f"# Header\n\n{para1}\n\n{para2}"

        result = split_text_into_chunks(text)

        assert len(result) == 1
        assert para1 in result[0]
        assert para2 in result[0]

    def test_long_paragraph_uses_char_window(self):
        long_paragraph = "x" * (MAX_VECTOR_CHUNK_CHARS * 3)

        result = split_text_into_chunks(long_paragraph)

        assert len(result) >= 3
        assert all(len(chunk) <= MAX_VECTOR_CHUNK_CHARS for chunk in result)

    def test_bullets_are_independent_chunks(self):
        text = "Intro\n- First fact\n  supporting detail\n- Second fact"

        result = split_text_into_chunks(text)

        assert result == [
            "Intro",
            "- First fact\n  supporting detail",
            "- Second fact",
        ]

    def test_sections_that_exceed_combined_limit_remain_separate(self):
        first_section = "a" * 500
        second_section = f"# Second\n{'b' * 500}"

        result = split_text_into_chunks(f"{first_section}\n{second_section}")

        assert result == [first_section, second_section]

    def test_long_section_flushes_prose_before_windowed_paragraph(self):
        long_paragraph = "x" * (MAX_VECTOR_CHUNK_CHARS + 100)

        result = split_text_into_chunks(f"Short introduction.\n\n{long_paragraph}")

        assert result[0] == "Short introduction."
        assert "".join(result[1:]).startswith("x" * MAX_VECTOR_CHUNK_CHARS)

    def test_long_section_splits_medium_paragraphs_at_chunk_limit(self):
        first_paragraph = "a" * 500
        second_paragraph = "b" * 500

        result = split_text_into_chunks(f"{first_paragraph}\n\n{second_paragraph}")

        assert result == [first_paragraph, second_paragraph]


class TestSemanticChunkHelpers:
    """Cover helper preconditions and Markdown list grouping directly."""

    def test_split_into_paragraphs_groups_bullet_continuations(self):
        result = semantic_chunking._split_into_paragraphs(
            "\n\n- First fact\ncontinuation\n- Second fact"
        )

        assert result == ["- First fact\ncontinuation", "- Second fact"]

    def test_empty_helper_inputs_return_no_chunks(self):
        assert semantic_chunking._split_long_section("") == []
        assert semantic_chunking._split_by_char_window("   ") == []


class TestBuildVectorChunkRecords:
    def test_produces_records_with_correct_keys(self):
        rows = [
            _make_row(
                row_type=SearchItemType.ENTITY.value,
                title="Test",
                permalink="test",
                content_snippet="content",
                row_id=42,
            )
        ]

        result = build_vector_chunk_records(rows)

        assert result.records
        for record in result.records:
            assert set(record) == {"chunk_key", "chunk_text", "source_hash"}
            assert record["chunk_key"].startswith("entity:")

    def test_chunk_key_includes_row_id(self):
        rows = [
            _make_row(
                row_type=SearchItemType.OBSERVATION.value,
                content_snippet="obs content",
                row_id=99,
            )
        ]

        result = build_vector_chunk_records(rows)

        assert any("99" in record["chunk_key"] for record in result.records)

    def test_duplicate_rows_collapse_to_unique_chunk_keys(self):
        rows = [
            _make_row(
                row_type=SearchItemType.ENTITY.value,
                title="Spec",
                permalink="spec",
                content_snippet="shared content",
                row_id=77,
            ),
            _make_row(
                row_type=SearchItemType.ENTITY.value,
                title="Spec",
                permalink="spec",
                content_snippet="shared content",
                row_id=77,
            ),
        ]

        result = build_vector_chunk_records(rows)

        assert result.duplicate_chunk_keys == 1
        assert len(result.records) == 1
        assert result.records[0]["chunk_key"] == "entity:77:0"


class TestBuildEntityFingerprint:
    def test_fingerprint_is_stable_across_record_order(self):
        first_record: VectorChunkRecord = {
            "chunk_key": "entity:1:0",
            "chunk_text": "First chunk",
            "source_hash": "first-hash",
        }
        second_record: VectorChunkRecord = {
            "chunk_key": "entity:1:1",
            "chunk_text": "Second chunk",
            "source_hash": "second-hash",
        }

        forward = build_entity_fingerprint([first_record, second_record])
        reversed_order = build_entity_fingerprint([second_record, first_record])

        assert forward == reversed_order

    def test_fingerprint_changes_with_source_hash(self):
        original: VectorChunkRecord = {
            "chunk_key": "entity:1:0",
            "chunk_text": "Same chunk",
            "source_hash": "first-hash",
        }
        changed: VectorChunkRecord = {**original, "source_hash": "second-hash"}

        assert build_entity_fingerprint([original]) != build_entity_fingerprint([changed])
