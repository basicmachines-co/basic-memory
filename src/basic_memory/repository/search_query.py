"""Shared full-text query preparation rules."""

import re

# Interrogative/function words contribute lexical noise when a strict
# full-text query is relaxed: "when OR did OR a" matches loud wrong documents
# that displace genuine results from the ranking window.
RELAXATION_STOPWORDS = frozenset(
    "a an and are as at be but by did do does for from had has have how i in is it of on "
    "or our that the their they this to was we were what when where which who whom whose why "
    "will with you your".split()
)
RELAXATION_CJK_PATTERN = re.compile(
    r"["
    r"\u1100-\u11ff"  # Hangul Jamo
    r"\u3040-\u30ff"  # Hiragana and Katakana
    r"\u3130-\u318f"  # Hangul Compatibility Jamo
    r"\u31f0-\u31ff"  # Katakana Phonetic Extensions
    r"\u3400-\u4dbf"  # CJK Unified Ideographs Extension A
    r"\u4e00-\u9fff"  # CJK Unified Ideographs
    r"\ua960-\ua97f"  # Hangul Jamo Extended-A
    r"\uac00-\ud7af"  # Hangul Syllables
    r"\ud7b0-\ud7ff"  # Hangul Jamo Extended-B
    r"\uf900-\ufaff"  # CJK Compatibility Ideographs
    r"\uff65-\uff9f"  # Halfwidth Katakana
    r"]"
)
RELAXATION_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
RELAXATION_EDGE_PUNCTUATION = "?!.,;:，。！？；：、"


def _dedupe_relaxation_words(words: list[str]) -> list[str]:
    """Preserve first-seen relaxed terms while removing duplicates case-insensitively."""
    deduped_terms: list[str] = []
    seen_terms: set[str] = set()
    for word in words:
        key = word.lower()
        if key in seen_terms:
            continue
        seen_terms.add(key)
        deduped_terms.append(word)
    return deduped_terms


def _split_relaxation_words(search_text: str) -> list[str]:
    """Split whitespace-delimited relaxed terms, preserving CJK words."""
    words = [word.strip(RELAXATION_EDGE_PUNCTUATION) for word in search_text.split()]
    return [word for word in words if word]


def relaxed_query_words(search_text: str | None) -> list[str] | None:
    """Return content-bearing words for OR-relaxing a strict full-text query.

    Returns None when relaxation must not apply. These eligibility rules match
    SearchService._is_relaxed_fts_fallback_eligible so the hybrid FTS branch
    relaxes exactly the same query shapes as the service-level FTS path:

    - empty / quoted / explicit-boolean queries (user intent is not
      second-guessed);
    - fewer than three alphanumeric tokens (short queries like "New Feature"
      over-broaden under OR — and in hybrid the relaxed FTS-only rows normalize
      to 1.0 and can outrank the vector result the user wanted);
    - CJK terms separated by whitespace can relax with two or more terms because
      the ASCII token gate would otherwise suppress the fallback entirely;
    - any pure-digit token ("root note 1", "SPEC 16") — identifier-like queries
      over-broaden and create false positives under OR.
    """
    if not search_text:
        return None
    stripped = search_text.strip()
    if '"' in stripped or any(op in f" {stripped} " for op in (" AND ", " OR ", " NOT ")):
        return None

    # Eligibility checks run on raw alphanumeric tokens (parity with the
    # service), before stopword filtering.
    cjk_words = _split_relaxation_words(stripped)
    has_cjk_term = any(RELAXATION_CJK_PATTERN.search(word) for word in cjk_words)

    if has_cjk_term:
        if len(cjk_words) < 2 or any(word.isdigit() for word in cjk_words):
            return None
        pruned_words = [
            word
            for word in cjk_words
            if word.isalnum() and word.lower() not in RELAXATION_STOPWORDS
        ]
        relaxed_words = _dedupe_relaxation_words(pruned_words)
        # Trigger: punctuation/stopword pruning or deduplication leaves only one term.
        # Why: the raw whitespace count can make an identifier-like mixed query
        # appear multi-term even though only one backend-safe CJK prefix remains.
        # Outcome: preserve the short-query guard after pruning to avoid a broad retry.
        return relaxed_words if len(relaxed_words) >= 2 else None

    tokens = RELAXATION_ASCII_TOKEN_PATTERN.findall(stripped.lower())
    if len(tokens) < 3 or any(token.isdigit() for token in tokens):
        return None
    pruned_words = [token for token in tokens if token not in RELAXATION_STOPWORDS]
    return _dedupe_relaxation_words(pruned_words or tokens) or None
