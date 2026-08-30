"""Application-owned lexical analysis for scripts without reliable word boundaries."""

import hashlib
import unicodedata
from dataclasses import dataclass


_SCRIPT_BOUNDARY = "bm_script_boundary"
MIXED_WORD_PREFIX_LIMIT = 64


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
            (0x3005, 0x3007),  # Ideographic iteration, closing, and zero marks
            (0x3021, 0x3029),  # Hangzhou numerals
            (0x3031, 0x3035),  # Vertical kana repeat marks
            (0x3038, 0x303B),  # Hangzhou tens and vertical iteration mark
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
            (0x1AFF0, 0x1AFFF),  # Katakana extended B
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
        # Join controls shape neighboring script characters without introducing a searchable
        # unit. Keeping the current run open preserves ordered matching across the control.
        if character in {"\u200c", "\u200d"}:
            continue
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


def mixed_token_word_terms(text: str) -> tuple[str, ...]:
    """Encode prefix-searchable word fragments and their order around script runs."""
    terms: list[str] = []
    for token in text.split():
        normalized_token = unicodedata.normalize("NFKC", token)
        if not any(is_script_search_character(character) for character in normalized_token):
            continue

        components: list[tuple[str, str]] = []
        component_kind: str | None = None
        component_characters: list[str] = []
        for character in normalized_token:
            if character in {"\u200c", "\u200d"}:
                continue
            if component_characters and unicodedata.category(character) in {"Mn", "Mc", "Me"}:
                component_characters.append(character)
                continue

            next_kind = (
                "script"
                if is_script_search_character(character)
                else "word"
                if character.isalnum()
                else None
            )
            if next_kind != component_kind and component_characters:
                components.append((component_kind or "word", "".join(component_characters)))
                component_characters = []
            component_kind = next_kind
            if next_kind is not None:
                component_characters.append(character)
        if component_characters:
            components.append((component_kind or "word", "".join(component_characters)))

        word_prefixes: dict[int, tuple[str, ...]] = {}
        for index, (kind, value) in enumerate(components):
            if kind != "word":
                continue

            normalized_word = value.casefold()
            prefix_count = min(len(normalized_word), MIXED_WORD_PREFIX_LIMIT)
            prefixes = tuple(normalized_word[:length] for length in range(1, prefix_count + 1))
            word_prefixes[index] = prefixes
            terms.extend(
                f"bmword{hashlib.sha256(prefix.encode()).hexdigest()}" for prefix in prefixes
            )
            if len(normalized_word) > MIXED_WORD_PREFIX_LIMIT:
                terms.append(f"bmwordexact{hashlib.sha256(normalized_word.encode()).hexdigest()}")

        # A word's direction and ordinal distance from the nearest script component preserve
        # complete component order without multiplying every prefix by every script gram.
        after_distance = 0
        has_script_before = False
        for index, (kind, _) in enumerate(components):
            if kind == "script":
                after_distance = 0
                has_script_before = True
                continue
            if not has_script_before:
                continue
            after_distance += 1
            terms.extend(
                "bmpos" + hashlib.sha256(f"after\0{after_distance}\0{prefix}".encode()).hexdigest()
                for prefix in word_prefixes[index]
            )

        before_distance = 0
        has_script_after = False
        for index in range(len(components) - 1, -1, -1):
            kind, _ = components[index]
            if kind == "script":
                before_distance = 0
                has_script_after = True
                continue
            if not has_script_after:
                continue
            before_distance += 1
            terms.extend(
                "bmpos"
                + hashlib.sha256(f"before\0{before_distance}\0{prefix}".encode()).hexdigest()
                for prefix in word_prefixes[index]
            )
    return tuple(dict.fromkeys(terms))


def build_script_ngrams(*texts: str | None) -> str:
    """Build portable index text without depending on a database tokenizer."""
    gram_runs: list[str] = []
    for text in texts:
        if not text:
            continue
        for run in script_runs(text):
            # Unigrams make a single-character query searchable inside a longer run. Keeping
            # bigrams together after them preserves ordered phrase matching for longer queries.
            index_terms = run if len(run) == 1 else (*run, *script_run_grams(run))
            gram_runs.append(" ".join(index_terms))
        gram_runs.extend(mixed_token_word_terms(text))
    return f" {_SCRIPT_BOUNDARY} ".join(gram_runs)


def analyze_script_query(text: str) -> ScriptQuery:
    """Split a natural-language query into word text and ordered script grams."""
    normalized = unicodedata.normalize("NFKC", text)
    # Explicit Boolean expressions keep the existing backend parser. Splitting one
    # operand into a second SQL channel would otherwise change OR/NOT semantics.
    # Compatibility forms are natural-language text because neither backend parses
    # their normalized equivalents as operators.
    padded_text = f" {text} "
    if '"' in text or any(f" {operator} " in padded_text for operator in ("AND", "OR", "NOT")):
        return ScriptQuery(word_text=text, gram_phrases=())

    # Mixed tokens use the same application-owned auxiliary channel as script grams. This
    # avoids assuming that either backend exposes word fragments on both sides of a script run.
    word_tokens = [
        token.lower() if token in {"AND", "OR", "NOT"} else token
        for token in text.split()
        if not any(
            is_script_search_character(character)
            for character in unicodedata.normalize("NFKC", token)
        )
        and any(character.isalnum() for character in unicodedata.normalize("NFKC", token))
    ]
    gram_phrases = (
        *(script_run_grams(run) for run in script_runs(normalized)),
        *((term,) for term in mixed_token_word_terms(text)),
    )
    # Preserve punctuation-only input as an explicit backend query. Dropping it would make
    # the repositories confuse user text with the intentional no-predicate wildcard path.
    word_text = " ".join(word_tokens) or (text if not gram_phrases else None)
    return ScriptQuery(
        word_text=word_text,
        gram_phrases=gram_phrases,
    )
