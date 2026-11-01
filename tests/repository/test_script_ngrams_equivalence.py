"""Golden equivalence between fast script n-grams and the original per-character scanner.

The reference functions below are the implementation that shipped before script classification
moved to a compiled regex. Search rows already stored in every database were built by it, so the
fast path must produce byte-identical index text or existing rows would stop matching queries.
"""

import random
import unicodedata

import pytest

from basic_memory.repository.script_ngrams import (
    build_script_ngrams,
    is_script_search_character,
    script_runs,
)


# --- Frozen reference implementation ---


def reference_is_script_search_character(character: str) -> bool:
    codepoint = ord(character)
    return any(
        lower <= codepoint <= upper
        for lower, upper in (
            (0x1100, 0x11FF),
            (0x0E00, 0x0EFF),
            (0x0F00, 0x0FFF),
            (0x1000, 0x109F),
            (0x1780, 0x17FF),
            (0x19E0, 0x19FF),
            (0x3005, 0x3007),
            (0x3021, 0x3029),
            (0x3031, 0x3035),
            (0x3038, 0x303B),
            (0x3040, 0x30FF),
            (0x3100, 0x318F),
            (0x31A0, 0x31BF),
            (0x31F0, 0x31FF),
            (0x3400, 0x4DBF),
            (0x4E00, 0x9FFF),
            (0xA960, 0xA97F),
            (0xA9E0, 0xA9FF),
            (0xAA60, 0xAA7F),
            (0xAC00, 0xD7AF),
            (0xD7B0, 0xD7FF),
            (0xF900, 0xFAFF),
            (0x1AFF0, 0x1AFFF),
            (0x1B000, 0x1B16F),
            (0x20000, 0x2FFFF),
            (0x30000, 0x323AF),
        )
    )


def reference_script_runs(text: str) -> tuple[tuple[str, ...], ...]:
    runs: list[tuple[str, ...]] = []
    current: list[str] = []
    for character in unicodedata.normalize("NFKC", text):
        if character in {"‌", "‍"}:
            continue
        if current and unicodedata.category(character) in {"Mn", "Mc", "Me"}:
            current[-1] += character
            continue
        if reference_is_script_search_character(character):
            current.append(character)
            continue
        if current:
            runs.append(tuple(current))
            current = []
    if current:
        runs.append(tuple(current))
    return tuple(runs)


def reference_build_script_ngrams(*texts: str | None) -> str:
    gram_runs: list[str] = []
    for text in texts:
        if not text:
            continue
        for run in reference_script_runs(text):
            if len(run) == 1:
                index_terms = run
            else:
                bigrams = tuple(first + second for first, second in zip(run, run[1:]))
                index_terms = (*run, *bigrams)
            gram_runs.append(" ".join(index_terms))
    return " bm_script_boundary ".join(gram_runs)


# --- Golden cases ---

GOLDEN_TEXTS = {
    "empty": "",
    "latin": "The quick brown fox jumps over the lazy dog. Résumé, naïve café.",
    "latin_combining_only": "é à ñ",
    "cjk": "即适者生存的讨论与黑猫，時々更新",
    "japanese": "サバイバルとひらがな、ｶﾀｶﾅ半角",
    "korean": "생존 경쟁 ᄀᄁ",
    "thai_marks": "ภาษาไทย กั้น ที่นี่",
    "khmer_join_controls": "ក‌ខ‍គ",
    "tibetan_myanmar": "བོད་ཡིག မြန်မာ",
    "mixed": "OpenAI 适者生存, foo適者bar, サバイバル and ภาษาไทย!",
    "emoji": "😀 适者 🎉生存👍🏽 家族👨‍👩‍👧",
    "latin_mark_after_script": "ก́ข 漢⃝字 漢\N{VARIATION SELECTOR-1}",
    "leading_marks_and_controls": "́‍漢‌́字",
    "compatibility": "ＡＢＣ ﬁnance ㍿ ㈱ 适者",
    "range_edges": "෿฀໿ༀ〄々〇〈\U000323af\U000323b0",
    "supplementary": "\U00020000\U0002a6d6\U0001aff0\U0001aff3\U0001b000",
    "very_long_mixed": ("Notes about 适者生存 and ภาษาไทย with emoji 😀. " * 5_000),
}


@pytest.mark.parametrize("name", sorted(GOLDEN_TEXTS))
def test_build_script_ngrams_matches_reference_byte_for_byte(name: str) -> None:
    text = GOLDEN_TEXTS[name]

    assert script_runs(text) == reference_script_runs(text)
    assert build_script_ngrams(text).encode() == reference_build_script_ngrams(text).encode()


def test_build_script_ngrams_matches_reference_across_multiple_texts() -> None:
    texts = (None, GOLDEN_TEXTS["cjk"], "", GOLDEN_TEXTS["mixed"], GOLDEN_TEXTS["thai_marks"])

    assert build_script_ngrams(*texts) == reference_build_script_ngrams(*texts)


def test_script_character_classification_matches_reference_for_every_relevant_codepoint() -> None:
    # Every range ends below U+32400, and surrogates cannot appear in normalized text.
    for codepoint in range(0x33000):
        character = chr(codepoint)
        assert is_script_search_character(character) == reference_is_script_search_character(
            character
        ), f"U+{codepoint:04X}"


def test_script_runs_match_reference_on_random_mixed_script_text() -> None:
    # Bias the alphabet toward the tricky boundaries: marks inside and outside script ranges,
    # join controls, width variants, emoji, and the first codepoint past each range.
    alphabet = [
        *(chr(codepoint) for codepoint in range(0x20, 0x250)),
        *(chr(codepoint) for codepoint in range(0x300, 0x370)),
        *(chr(codepoint) for codepoint in range(0x0E00, 0x0F00)),
        *(chr(codepoint) for codepoint in range(0x1780, 0x1800)),
        *(chr(codepoint) for codepoint in range(0x3000, 0x3100)),
        *(chr(codepoint) for codepoint in range(0x4E00, 0x4E40)),
        *(chr(codepoint) for codepoint in range(0xAC00, 0xAC40)),
        *(chr(codepoint) for codepoint in range(0xFF00, 0xFFEF)),
        "‌",
        "‍",
        "⃝",
        "ः",
        "\U0001f600",
        "\U00020000",
        "\U000323af",
        "\U000323b0",
    ]
    randomizer = random.Random(20261101)
    for _ in range(5_000):
        text = "".join(randomizer.choice(alphabet) for _ in range(randomizer.randint(0, 40)))
        assert script_runs(text) == reference_script_runs(text), repr(text)
        assert build_script_ngrams(text) == reference_build_script_ngrams(text), repr(text)
