"""Application-owned lexical analysis for scripts without reliable word boundaries."""

import unicodedata
from dataclasses import dataclass


_SCRIPT_BOUNDARY = "bm_script_boundary"


@dataclass(frozen=True, slots=True)
class ScriptQuery:
    """The word-oriented and script-oriented parts of one user query."""

    word_text: str | None
    gram_phrases: tuple[tuple[str, ...], ...]


def is_script_search_character(character: str) -> bool:
    codepoint = ord(character)
    return any(
        lower <= codepoint <= upper
        for lower, upper in (
            (0x1100, 0x11FF),  # Hangul Jamo
            (0x0E00, 0x0EFF),  # Thai and Lao
            (0x0F00, 0x0FFF),  # Tibetan
            (0x1000, 0x109F),  # Myanmar
            (0x1780, 0x17FF),  # Khmer
            (0x19E0, 0x19FF),  # Khmer symbols
            (0x3040, 0x30FF),  # Hiragana and Katakana
            (0x3100, 0x318F),  # Bopomofo and Hangul compatibility Jamo
            (0x31A0, 0x31BF),  # Bopomofo extended
            (0x31F0, 0x31FF),  # Katakana phonetic extensions
            (0x3400, 0x4DBF),  # CJK unified ideographs extension A
            (0x4E00, 0x9FFF),  # CJK unified ideographs
            (0xA960, 0xA97F),  # Hangul Jamo extended A
            (0xA9E0, 0xA9FF),  # Myanmar extended B
            (0xAA60, 0xAA7F),  # Myanmar extended A
            (0xAC00, 0xD7AF),  # Hangul syllables
            (0xD7B0, 0xD7FF),  # Hangul Jamo extended B
            (0xF900, 0xFAFF),  # CJK compatibility ideographs
            (0x1B000, 0x1B16F),  # Kana supplements and extensions
            (0x20000, 0x2FFFF),  # Supplementary CJK ideographs
            (0x30000, 0x323AF),  # CJK unified ideographs extensions G-H
        )
    )


def script_runs(text: str) -> tuple[tuple[str, ...], ...]:
    """Return normalized script runs as grapheme-like searchable units."""
    runs: list[tuple[str, ...]] = []
    current: list[str] = []
    for character in unicodedata.normalize("NFKC", text):
        if current and unicodedata.category(character) in {"Mn", "Mc", "Me"}:
            current[-1] += character
            continue
        if is_script_search_character(character):
            current.append(character)
            continue
        if current:
            runs.append(tuple(current))
            current = []
    if current:
        runs.append(tuple(current))
    return tuple(runs)


def script_run_grams(run: tuple[str, ...]) -> tuple[str, ...]:
    """Use bigrams for context and retain a searchable single-character run."""
    if len(run) == 1:
        return run
    return tuple(first + second for first, second in zip(run, run[1:], strict=False))


def build_script_ngrams(*texts: str | None) -> str:
    """Build portable index text without depending on a database tokenizer."""
    gram_runs = [
        " ".join(script_run_grams(run)) for text in texts if text for run in script_runs(text)
    ]
    return f" {_SCRIPT_BOUNDARY} ".join(gram_runs)


def analyze_script_query(text: str) -> ScriptQuery:
    """Split a natural-language query into word text and ordered script grams."""
    normalized = unicodedata.normalize("NFKC", text)
    # Explicit Boolean expressions keep the existing backend parser. Splitting one
    # operand into a second SQL channel would otherwise change OR/NOT semantics.
    if {token.upper() for token in normalized.split()} & {"AND", "OR", "NOT"}:
        return ScriptQuery(word_text=normalized, gram_phrases=())

    word_characters: list[str] = []
    in_script_run = False
    for character in normalized:
        if in_script_run and unicodedata.category(character) in {"Mn", "Mc", "Me"}:
            continue
        if is_script_search_character(character):
            if not in_script_run:
                word_characters.append(" ")
            in_script_run = True
            continue
        in_script_run = False
        word_characters.append(character)

    word_tokens = [
        token
        for token in "".join(word_characters).split()
        if any(character.isalnum() for character in token)
    ]
    word_text = " ".join(word_tokens) or None
    return ScriptQuery(
        word_text=word_text,
        gram_phrases=tuple(script_run_grams(run) for run in script_runs(normalized)),
    )
