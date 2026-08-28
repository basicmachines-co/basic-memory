"""Shared full-text query preparation rules."""

import re
import unicodedata

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
    r"]+"
)
RELAXATION_EDGE_PUNCTUATION = "?!.,;:，。！？；：、"
# Written inside a word (Persian U+200C, Indic conjuncts) rather than between words.
# Format characters (Unicode Cf) are invisible and, with one exception, sit
# inside a word: soft hyphen from copied text, the Persian and Indic joiners,
# bidi marks, the Mongolian vowel separator, word joiners. Counting any of them
# as a separator splits one word into several tokens, which is the direction
# that defeats the guards below — grouping can only lower a token count, never
# inflate it past them.
#
# U+200B is the exception: zero-width space marks word *boundaries* in Thai and
# Khmer, so it must keep splitting, or a whole phrase collapses into one token
# and relaxation switches off for the one form of those scripts that reaches
# the guard at all.
RELAXATION_WORD_SEPARATOR_FORMATS = "\u200b"
# Of the characters kept inside a token, only these two are orthography: the
# Persian and Indic joiners are written in the text and therefore sit in the
# index too, so a relaxed term must keep them or it stops matching. Every other
# format character is a rendering artifact — a soft hyphen from a paginated
# document, a bidi hint, a stray BOM — absent from the stored note, so carrying
# it into the term is what stops the term matching.
RELAXATION_ORTHOGRAPHIC_JOINERS = "\u200c\u200d"
# Punctuation written inside a word, joined only between two letters. This is
# the whole of UAX #29 MidLetter, MidNumLet and Single_Quote — plus U+05F3, which
# the standard classes as a letter — minus two families that carry structure in
# this project rather than inside words:
#
#   colons      U+003A U+FE13 U+FE55 U+FF1A   "tag:example" is query syntax
#   full stops  U+002E U+2024 U+FE52 U+FF0E   permalinks and file names
#
# Joining across those would merge a qualifier with its value, or a name with its
# extension, into one term. Everything else in the class is here, so a new
# member is a change to the standard rather than an oversight.
RELAXATION_WORD_INTERNAL_PUNCTUATION = "'\u2018\u2019\uff07\u00b7\u0387\u055f\u05f3\u05f4\u2027"


def _is_word_internal_format(char: str) -> bool:
    """Whether an invisible format character belongs to the word around it."""
    return unicodedata.category(char) == "Cf" and char not in RELAXATION_WORD_SEPARATOR_FORMATS


def _strip_trailing_formats(token: str) -> str:
    """Drop format characters left at a token's end, where they separate rather than join."""
    # Indexed rather than sliced: trimming one character at a time copies the
    # shrinking token at every step, which is quadratic in a run of trailing
    # format characters.
    end = len(token)
    while end and _is_word_internal_format(token[end - 1]):
        end -= 1
    return token[:end]


def _is_attached(char: str) -> bool:
    """Whether a character hangs off the one before it rather than standing alone."""
    return _is_word_internal_format(char) or unicodedata.category(char).startswith("M")


def _base_before(current: list[str]) -> bool:
    """Whether the token so far ends in a letter, looking past what hangs off it.

    Pointed Hebrew and decomposed Latin put a mark between the letter and the
    punctuation, so reading only the last character sees the mark and splits a
    word the joining rule is meant to keep whole.
    """
    for char in reversed(current):
        if _is_attached(char):
            continue
        return char.isalpha()
    return False


def _base_after(text: str, index: int) -> bool:
    """Whether a letter follows the punctuation, looking past what hangs off it."""
    # Indexed rather than sliced: a slice copies the rest of the query at every
    # joiner, which makes tokenizing a long query quadratic in its length.
    for position in range(index + 1, len(text)):
        char = text[position]
        if _is_attached(char):
            continue
        return char.isalpha()
    return False


def _is_token_continuation(text: str, index: int, current: list[str]) -> bool:
    """Whether a non-alphanumeric character belongs to the word being read.

    Combining marks, invisible word-internal format characters, and apostrophes are written inside
    a word but are not alphanumeric, so a naive scan treats them as separators
    and splits one orthographic word into several tokens.

    An apostrophe counts only between two letters. That keeps "п’ять" whole while
    leaving "SPEC 16's" split, so the digit stays a token of its own and the
    numeric-identifier guard still rejects the query.
    """
    char = text[index]
    if _is_word_internal_format(char) or unicodedata.category(char).startswith("M"):
        return True
    if char in RELAXATION_WORD_INTERNAL_PUNCTUATION:
        return _base_before(current) and _base_after(text, index)
    return False


def relaxation_word_tokens(text: str) -> list[str]:
    """Split text into word tokens for the relaxation eligibility guards.

    A token is a run of alphanumeric characters together with the combining
    marks, join controls, and apostrophes written inside it. Counting this way matters because
    an ASCII-only rule saw zero tokens in Cyrillic, Greek, Hebrew, Arabic,
    Armenian, and Georgian queries, so the three-token guard below rejected every
    one of them and the hybrid FTS branch silently contributed nothing.

    Counting characters that live inside a word as separators is just as wrong in
    the other direction: it cuts abugidas (Devanagari, Thai), decomposed text,
    and Persian or Indic words joined by U+200C/U+200D into fragments. One word
    then looks like several tokens, clears the three-token guard, and relaxes
    into a broad OR of fragments — the opposite of what the guard is for.

    Scripts normally written without spaces between words — Thai, Lao, Khmer —
    are counted, but a whole phrase arrives as a single token and so never
    reaches the three-token guard. They are not in RELAXATION_CJK_PATTERN either,
    so nothing relaxes for them. Fixing that needs real word segmentation.
    """
    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        # A trailing format character is word-internal by definition, so a token
        # that ends in one is really a word followed by a separator.
        token = _strip_trailing_formats("".join(current))
        if token:
            tokens.append(token)
        current.clear()

    for index, char in enumerate(text):
        # A leading mark, join control, or apostrophe has no base character to
        # attach to, so it cannot open a token; that keeps stray punctuation from
        # forming fragment-only terms.
        if char.isalnum() or (current and _is_token_continuation(text, index, current)):
            current.append(char)
        elif current:
            flush()
    flush()
    return tokens


def _token_core(token: str) -> str:
    """The token without the characters that only ever attach to another one.

    Combining marks and format characters are kept inside a token so a word is
    counted once, but they must not disguise what the token *is*: a keycap digit
    is not `isnumeric()` as a whole string, which would walk an identifier-like
    query straight past the numeric guard.
    """
    return "".join(
        char
        for char in token
        if not _is_word_internal_format(char) and not unicodedata.category(char).startswith("M")
    )


def _is_numeric_token(token: str) -> bool:
    """Whether a token is a bare number, and so identifier-like rather than a word.

    Classified by Unicode category, not by `isdigit()`/`isnumeric()`. Both of
    those answer True for Han numerals, which are category Lo — letters, and
    ordinary content words in CJK prose. Rejecting them would switch relaxation
    off for the queries #1022 turned it on for.

    Everything in category N is a number character: ASCII and Arabic-Indic
    digits (Nd), Roman numerals (Nl), vulgar fractions (No). A token made only
    of those is the "SPEC 16" shape the guard exists to catch, in any script.
    """
    core = _token_core(token)
    return bool(core) and all(unicodedata.category(char).startswith("N") for char in core)


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


def _relaxation_term_variants(word: str) -> list[str]:
    """Every form of a word that could match how the note happens to be stored.

    A format character is invisible, so the same word may be stored with it or
    without it, and the two index differently: a note holding "foo\u00adbar" is
    indexed as "foo" and "bar", one holding "foobar" as a single token. Neither
    term matches the other note, so both forms are emitted and the OR that
    relaxation already builds covers whichever the note actually has.

    Orthographic joiners are not stripped: they are written in the text, so the
    stored form has them and the cleaned variant would only add noise.
    """
    cleaned = "".join(
        char
        for char in word
        if char in RELAXATION_ORTHOGRAPHIC_JOINERS or not _is_word_internal_format(char)
    )
    if not cleaned:
        return []
    return [cleaned] if cleaned == word else [cleaned, word]


def _emit_relaxation_terms(words: list[str]) -> list[str]:
    """Expand the words into backend-ready terms, then drop duplicates."""
    return _dedupe_relaxation_words(
        [variant for word in words for variant in _relaxation_term_variants(word)]
    )


def relaxed_query_words(search_text: str | None) -> list[str] | None:
    """Return content-bearing words for OR-relaxing a strict full-text query.

    Returns None when relaxation must not apply. These eligibility rules match
    SearchService._is_relaxed_fts_fallback_eligible so the hybrid FTS branch
    relaxes exactly the same query shapes as the service-level FTS path:

    - empty / quoted / explicit-boolean queries (user intent is not
      second-guessed);
    - fewer than three word tokens (short queries like "New Feature"
      over-broaden under OR — and in hybrid the relaxed FTS-only rows normalize
      to 1.0 and can outrank the vector result the user wanted). Tokens are
      counted with relaxation_word_tokens, so scripts other than Latin reach the
      same guard instead of being read as zero tokens;
    - CJK terms separated by whitespace can relax with two or more terms because
      they are not whitespace-delimited the way the token guard assumes;
    - any numeric token ("root note 1", "SPEC 16", "SPEC Ⅻ") — identifier-like
      queries over-broaden and create false positives under OR. Numeric-ness is
      Unicode-wide, so Nl/No characters such as Ⅻ and ½ are caught too.
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
        if len(cjk_words) < 2 or any(_is_numeric_token(word) for word in cjk_words):
            return None
        pruned_words = [
            word
            for word in cjk_words
            if word.isalnum() and word.lower() not in RELAXATION_STOPWORDS
        ]
        relaxed_words = _emit_relaxation_terms(pruned_words)
        # Trigger: punctuation/stopword pruning or deduplication leaves only one term.
        # Why: the raw whitespace count can make an identifier-like mixed query
        # appear multi-term even though only one backend-safe CJK prefix remains.
        # Outcome: preserve the short-query guard after pruning to avoid a broad retry.
        return relaxed_words if len(relaxed_words) >= 2 else None

    tokens = relaxation_word_tokens(stripped.lower())
    if len(tokens) < 3 or any(_is_numeric_token(token) for token in tokens):
        return None
    pruned_words = [token for token in tokens if token not in RELAXATION_STOPWORDS]
    return _emit_relaxation_terms(pruned_words or tokens) or None


def contains_cjk(text: str) -> bool:
    """Whether text contains any character from the supported CJK ranges."""
    return RELAXATION_CJK_PATTERN.search(text) is not None


def cjk_bigram_tokens(run: str) -> tuple[str, ...]:
    """Overlapping two-character windows over one CJK run.

    Un-segmented CJK text has no whitespace between words, so finding a
    substring match that starts mid-run needs overlapping bigrams rather than
    whitespace-delimited tokens. A single character has no second character to
    pair with, so it is kept as a one-character token instead of vanishing.
    """
    if not run:
        return ()
    if len(run) == 1:
        return (run,)
    return tuple(run[index : index + 2] for index in range(len(run) - 1))


def cjk_search_tokens(*fields: str | None) -> str:
    """Space-joined bigram tokens for every CJK run across the given fields.

    Each field is scanned independently so a run can never cross a field
    boundary, and non-CJK substrings (Latin words, digits) never enter the
    auxiliary token stream.
    """
    tokens: list[str] = []
    for field in fields:
        if not field:
            continue
        for match in RELAXATION_CJK_PATTERN.finditer(field):
            tokens.extend(cjk_bigram_tokens(match.group(0)))
    return " ".join(tokens)
