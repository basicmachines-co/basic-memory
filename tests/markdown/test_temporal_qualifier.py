"""Parsing and round-tripping SPEC-82 temporal qualifiers on observations.

Three rules shape every test here:

* **One grammar.** `@[kind]<range-literal>` for a precise interval, `@[kind:]<date>`
  for an unquoted point, and `@[kind:]"<date>"` for a quoted one. The kind is optional
  in all three; an unquoted point that omits it must begin with a digit.
* **Silent when it is not time.** If the payload does not read as a date, the token is
  ordinary content and nothing is reported. Prose is full of `@`, and diagnosing every
  one of them would be noise.
* **Diagnostics only where the author plainly meant a qualifier.** An unknown kind, an
  unterminated quote, and an unquoted date the one-token rule truncated are each
  reported, because each one has a fix the message can name.

And in every case a qualifier that was not accepted is **never dropped**: its text
stays in the observation content, so the line indexes and round-trips exactly as it did
before valid time existed.
"""

from datetime import datetime, timedelta

import pytest

from basic_memory import config as config_module
from basic_memory.config import ConfigManager
from basic_memory.markdown.entity_parser import parse
from basic_memory.markdown.schemas import Observation
from basic_memory.markdown.temporal_qualifier import parse_temporal_qualifier
from basic_memory.temporal import DateOrder, TemporalRangeAxis, TimeKind


@pytest.fixture(autouse=True)
def isolated_config(config_home, monkeypatch):
    """Point config resolution at a temp HOME for the whole module.

    The point form consults `date_order`, so parsing a qualifier reads configuration.
    `config_home` patches HOME; resetting the process cache keeps one test's config
    from leaking into the next.
    """
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    monkeypatch.setattr(config_module, "_CONFIG_MTIME", None)
    monkeypatch.setattr(config_module, "_CONFIG_SIZE", None)
    return config_home


def _observation(line: str) -> Observation:
    """Parse a single observation line through the real markdown pipeline."""
    [observation] = parse(line).observations
    return observation


def _refusal(line: str) -> Observation:
    """Parse a line whose qualifier must be refused, and assert the shared contract."""
    observation = _observation(line)
    assert observation.temporal == []
    assert observation.temporal_error is not None
    return observation


# --- Acceptance 1: undated notes are untouched ---


def test_observation_without_qualifier_parses_byte_identically():
    """A note that asserts no valid time behaves exactly as it did before SPEC-82."""
    observation = _observation("- [decision] The cache layer will use Redis. #infra (agreed)")

    assert observation.category == "decision"
    assert observation.content == "The cache layer will use Redis. #infra"
    assert observation.tags == ["infra"]
    assert observation.context == "agreed"
    assert observation.temporal == []
    assert observation.temporal_error is None
    assert str(observation) == "- [decision] The cache layer will use Redis. #infra (agreed)"


# --- Acceptance 2: round trip preserves kind and bounds ---


@pytest.mark.parametrize(
    "qualifier",
    [
        # The range literal: the precise form, unchanged by the point form's arrival.
        "@effective[2026-06-10,2026-07-27)",
        "@effective(2026-06-10,2026-07-27]",
        "@effective[2026-06-10,2026-07-27]",
        "@effective(2026-06-10,2026-07-27)",
        "@effective[2026-06-10,)",
        "@effective(,2026-07-27)",
        "@valid[2026-01-01,2026-12-31)",
        "@occurred[2026-07-27T18:42:00Z,2026-07-27T19:00:00Z)",
        "@due[2026-07-27T18:42:00+02:00,)",
        "@mentioned[2026-07-27T18:42:00.123456Z,2026-07-28T00:00:00Z)",
        "@[2026-06-10,2026-07-27)",
        # The point: the convenient form, with and without a kind.
        "@effective:2026-07-27",
        "@occurred:2026-07-27T18:42:00Z",
        "@due:2026-07",
        "@2026-07-27",
        "@2026-07",
        "@2026",
        "@10/07/2026",
    ],
)
def test_qualifier_round_trips_verbatim(qualifier: str):
    """Serializing a parsed observation replays the author's exact qualifier text.

    `valid_during` holds normalized bounds -- UTC, microsecond precision, a canonical
    interval -- but the author's own spelling is what gets written back, so a
    parse/serialize cycle never rewrites their file.
    """
    line = f"- [decision] {qualifier} The cache layer will use Redis."

    observation = _observation(line)

    [assertion] = observation.temporal
    assert observation.temporal_error is None
    assert observation.content == "The cache layer will use Redis."
    assert assertion.source_text == qualifier
    assert assertion.extractor == "observation"
    assert str(observation) == line


def test_qualifier_carries_its_kind_and_bounds():
    """The parsed assertion is the interval the author wrote, of the kind they named."""
    observation = _observation(
        "- [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis."
    )

    [assertion] = observation.temporal
    assert assertion.time_kind is TimeKind.EFFECTIVE
    assert assertion.valid_during.axis is TemporalRangeAxis.DATE
    assert assertion.valid_during.lower == "2026-06-10"
    assert assertion.valid_during.upper == "2026-07-27"
    assert assertion.valid_during.lower_inclusive is True
    assert assertion.valid_during.upper_inclusive is False
    assert str(assertion.valid_during) == "[2026-06-10,2026-07-27)"


def test_a_closed_qualifier_is_stored_half_open_without_rewriting_the_line():
    """The two forms coexist: canonical bounds for the index, the author's text on disk.

    `[2026-06-10,2026-07-27]` means "through July 27", which the discrete canonical form
    spells `[2026-06-10,2026-07-28)`. That normalization is the projection's business --
    `source_text` keeps the author's words, so serializing the note writes the file back
    exactly as they wrote it.
    """
    line = "- [decision] @effective[2026-06-10,2026-07-27] The cache layer will use Redis."

    observation = _observation(line)

    [assertion] = observation.temporal
    assert assertion.source_text == "@effective[2026-06-10,2026-07-27]"
    assert str(assertion.valid_during) == "[2026-06-10,2026-07-28)"
    assert assertion.valid_during.upper_inclusive is False
    assert str(observation) == line


def test_qualifier_is_peeled_before_context_and_tags():
    """Peel order matters: the context rule would otherwise steal a `)` qualifier.

    An exclusive-upper qualifier ends in `)`, and the context rule is a bare
    suffix match, so parsing context first would claim the qualifier and leave the
    observation content empty -- which the plugin then drops outright.
    """
    observation = _observation(
        "- [decision] @effective(2026-06-10,2026-07-27] Use Redis #infra (agreed)"
    )

    [assertion] = observation.temporal
    assert assertion.source_text == "@effective(2026-06-10,2026-07-27]"
    assert observation.content == "Use Redis #infra"
    assert observation.context == "agreed"
    # Qualifier digits are not tags: the peel happens before the tag scan.
    assert observation.tags == ["infra"]


def test_qualifier_alone_on_the_line_still_parses():
    """A qualifier with no trailing context is the common case, not an edge case."""
    observation = _observation("- [decision] @effective(2026-06-10,2026-07-27] Use Redis")

    [assertion] = observation.temporal
    assert assertion.source_text == "@effective(2026-06-10,2026-07-27]"
    assert observation.content == "Use Redis"
    assert observation.context is None


# --- The point form: what each precision means ---


@pytest.mark.parametrize(
    ("qualifier", "literal", "axis"),
    [
        # A year and a month are periods the author delimited by writing them.
        ("@2026", "[2026-01-01,2027-01-01)", TemporalRangeAxis.DATE),
        ("@2026-06", "[2026-06-01,2026-07-01)", TemporalRangeAxis.DATE),
        # A date says when something started and leaves it open.
        ("@2026-06-10", "[2026-06-10,)", TemporalRangeAxis.DATE),
        # So does a moment, on the instant axis.
        (
            "@2026-06-10T14:00:00",
            "[2026-06-10T14:00:00.000000Z,)",
            TemporalRangeAxis.INSTANT,
        ),
        (
            "@2026-06-10T14:00:00Z",
            "[2026-06-10T14:00:00.000000Z,)",
            TemporalRangeAxis.INSTANT,
        ),
        (
            "@2026-06-10T14:00:00+02:00",
            "[2026-06-10T12:00:00.000000Z,)",
            TemporalRangeAxis.INSTANT,
        ),
    ],
)
def test_point_qualifier_canonicalizes_to_the_span_its_precision_covers(
    qualifier: str, literal: str, axis: TemporalRangeAxis
):
    observation = _observation(f"- [decision] {qualifier} The cutover ran.")

    [assertion] = observation.temporal
    assert str(assertion.valid_during) == literal
    assert assertion.valid_during.axis is axis


def test_a_point_with_no_kind_is_filed_as_valid_time():
    """`@2026-06-10` says when the statement holds, without narrowing how."""
    observation = _observation("- [decision] @2026-06-10 The cache layer will use Redis.")

    [assertion] = observation.temporal
    assert assertion.time_kind is TimeKind.VALID


def test_a_range_literal_with_no_kind_is_filed_as_valid_time():
    """The kind is optional in both forms, and defaults the same way in both."""
    observation = _observation("- [decision] @[2026-06-10,2026-07-27) Use Redis.")

    [assertion] = observation.temporal
    assert assertion.time_kind is TimeKind.VALID
    assert str(assertion.valid_during) == "[2026-06-10,2026-07-27)"


@pytest.mark.parametrize(
    ("qualifier", "kind"),
    [
        ("@effective:2026-06-10", TimeKind.EFFECTIVE),
        ("@occurred:2026-06-10", TimeKind.OCCURRED),
        ("@due:2026-06-10", TimeKind.DUE),
        ("@mentioned:2026-06-10", TimeKind.MENTIONED),
        ("@valid:2026-06-10", TimeKind.VALID),
    ],
)
def test_point_qualifier_names_its_kind_with_a_colon(qualifier: str, kind: TimeKind):
    """`:` separates kind from date; a date can start with a letter, so it is needed."""
    observation = _observation(f"- [decision] {qualifier} The cutover ran.")

    [assertion] = observation.temporal
    assert assertion.time_kind is kind
    assert str(assertion.valid_during) == "[2026-06-10,)"


def test_a_point_with_a_kind_accepts_a_relative_date():
    """With a kind the author has said what they mean, so any readable date is taken.

    Relative wording resolves at parse time and is re-resolved on every index pass.
    That is documented behavior, not a mistake to warn about.
    """
    observation = _observation("- [decision] @occurred:yesterday The cutover ran.")

    [assertion] = observation.temporal
    yesterday = datetime.now().date() - timedelta(days=1)
    assert assertion.valid_during.lower == yesterday.isoformat()
    assert observation.content == "The cutover ran."


@pytest.mark.parametrize(
    "qualifier",
    [
        # Words: dateparser reads several of these as months or years.
        "@yesterday",
        "@may",
        "@v2",
        "@june",
        # Too short to be a year: list markers and version numbers, which dateparser
        # would otherwise read as January, 2012, and March 5.
        "@1",
        "@12",
        "@3.5",
        "@5-3",
    ],
)
def test_a_point_with_no_kind_must_be_digit_led_and_year_wide(qualifier: str):
    """A bare `@token` that short is a mention, a version, or a list marker.

    Accepting what dateparser makes of these would silently file wrong valid time on
    ordinary prose. An author who really means one writes the kind: `@occurred:may`.
    """
    observation = _observation(f"- [decision] {qualifier} shipped the cutover.")

    assert observation.temporal == []
    assert observation.temporal_error is None
    assert observation.content.startswith(qualifier)


def test_a_word_point_is_read_only_when_it_names_a_specific_day():
    """A kind opens the form to words, but not to words that name only a period.

    `yesterday` resolves to one day and is taken. `may` resolves to a whole month, and
    a bare month name at the head of a line is either prose or -- worse -- the first
    token of `May 10, 2026`, where reading it would file May 2026 and leave `10, 2026`
    behind as content.
    """
    day = _observation("- [decision] @occurred:yesterday The cutover ran.")
    [assertion] = day.temporal
    assert assertion.time_kind is TimeKind.OCCURRED
    assert day.content == "The cutover ran."

    period = _observation("- [decision] @occurred:may The cutover ran.")
    assert period.temporal == []
    assert period.temporal_error is None
    assert period.content.startswith("@occurred:may")


# --- The flexible vocabulary, as the qualifier grammar sees it ---
#
# `parse_authored_point` reads far more spellings than these (tests/test_temporal.py
# pins that vocabulary). The grammar is narrower on purpose, and this section is the
# boundary between the two: a qualifier is one whitespace-delimited token, because
# dateparser also reads `June 10, 2026 The` and `2026-06-10 The`, so there is no way to
# tell where a multi-word date stops without swallowing the author's prose.


@pytest.mark.parametrize(
    ("qualifier", "literal", "axis"),
    [
        # Single-token absolute dates, with a kind and without.
        ("@occurred:2026-06-10", "[2026-06-10,)", TemporalRangeAxis.DATE),
        ("@occurred:03/04/2026", "[2026-04-03,)", TemporalRangeAxis.DATE),
        (
            "@occurred:2026-06-10T10:00:00",
            "[2026-06-10T10:00:00.000000Z,)",
            TemporalRangeAxis.INSTANT,
        ),
        # A kind admits a word, as long as it names one day.
        ("@occurred:today", None, TemporalRangeAxis.DATE),
        ("@occurred:yesterday", None, TemporalRangeAxis.DATE),
    ],
)
def test_single_token_points_are_accepted(qualifier: str, literal: str | None, axis):
    observation = _observation(f"- [decision] {qualifier} The cutover ran.")

    [assertion] = observation.temporal
    assert observation.content == "The cutover ran."
    assert assertion.valid_during.axis is axis
    if literal is not None:
        assert str(assertion.valid_during) == literal


@pytest.mark.parametrize(
    ("qualifier", "reported"),
    [
        # Multi-word dates: only the first token reaches the reader, and each of these
        # first tokens is refused, so the whole line stays content rather than being
        # half-read. `@occurred:"June 10, 2026"` says it in one delimited token.
        ("@occurred:June 10, 2026", True),
        ("@occurred:Jan 15, 2024", True),
        ("@occurred:10 June 2026", False),
        ("@occurred:2 days ago", False),
        ("@occurred:last week", False),
    ],
)
def test_multi_word_dates_stay_content_whole(qualifier: str, reported: bool):
    """The reader understands these; the unquoted grammar cannot delimit them.

    What matters is that an undelimitable date is left *entirely* alone: no coarse
    assertion filed from its first token, and no words eaten out of the content. Whether
    the author additionally *hears* about it is the digit-follows signal's business,
    pinned below -- the line itself is untouched either way.
    """
    line = f"- [decision] {qualifier} The cutover ran."

    observation = _observation(line)

    assert observation.temporal == []
    assert observation.content == f"{qualifier} The cutover ran."
    assert str(observation) == line
    assert (observation.temporal_error is not None) is reported


def test_a_multi_word_date_is_read_up_to_its_first_token_when_that_token_stands_alone():
    """The one partial read the token rule allows, pinned so it is a known boundary.

    `2026-06-10` is a complete date by itself, so the qualifier claims it and the clock
    reading stays in the content. The assertion is coarser than the author meant -- a
    date, not an instant -- but it is not wrong, and nothing is lost from the line.
    """
    observation = _observation("- [decision] @occurred:2026-06-10 10:00 AM The cutover ran.")

    [assertion] = observation.temporal
    assert str(assertion.valid_during) == "[2026-06-10,)"
    assert assertion.valid_during.axis is TemporalRangeAxis.DATE
    assert observation.content == "10:00 AM The cutover ran."


# --- The quoted point: a date the author delimited ---
#
# Quotes are how a multi-word date is written. They move the token boundary from the
# next space to the closing quote, which is the whole reason the one-token guards do not
# apply inside them: the author said where the date ends, so nothing can be truncated.


@pytest.mark.parametrize(
    ("qualifier", "literal", "kind"),
    [
        ('@occurred:"June 10, 2026"', "[2026-06-10,)", TimeKind.OCCURRED),
        ('@effective:"10 June 2026"', "[2026-06-10,)", TimeKind.EFFECTIVE),
        # Month-only and year-only: coarse on purpose, and delimited, so they are read.
        ('@occurred:"June 2026"', "[2026-06-01,2026-07-01)", TimeKind.OCCURRED),
        # With no kind, exactly like the bare point form -- filed as valid time.
        ('@"June 10, 2026"', "[2026-06-10,)", TimeKind.VALID),
    ],
)
def test_quoted_point_reads_a_multi_word_date(qualifier: str, literal: str, kind: TimeKind):
    """The quoted form's payload goes to the date reader whole, spaces and all."""
    line = f"- [decision] {qualifier} The cutover ran."

    observation = _observation(line)

    [assertion] = observation.temporal
    assert observation.temporal_error is None
    assert assertion.time_kind is kind
    assert str(assertion.valid_during) == literal
    assert assertion.valid_during.axis is TemporalRangeAxis.DATE
    assert observation.content == "The cutover ran."
    # Quotes are part of the qualifier, so they round-trip with it.
    assert assertion.source_text == qualifier
    assert str(observation) == line


def test_a_quoted_relative_date_is_read_where_its_unquoted_form_is_not():
    """`2 days ago` always read fine; only the token rule kept it out."""
    quoted = _observation('- [decision] @occurred:"2 days ago" The cutover ran.')

    [assertion] = quoted.temporal
    two_days_ago = datetime.now().date() - timedelta(days=2)
    assert assertion.valid_during.lower == two_days_ago.isoformat()
    assert quoted.content == "The cutover ran."

    unquoted = _observation("- [decision] @occurred:2 days ago The cutover ran.")
    assert unquoted.temporal == []


def test_a_quoted_month_is_filed_where_the_specific_day_guard_refuses_it():
    """The guard exists to catch truncation, and a delimited value cannot be truncated.

    Unquoted, `June` is refused because it may be the head of `June 2026`. Quoted, the
    author has already said the date is exactly that month.
    """
    quoted = _observation('- [decision] @occurred:"June 2026" The cutover ran.')

    [assertion] = quoted.temporal
    assert str(assertion.valid_during) == "[2026-06-01,2026-07-01)"
    assert quoted.content == "The cutover ran."

    unquoted = _observation("- [decision] @occurred:June 2026 The cutover ran.")
    assert unquoted.temporal == []


def test_a_quoted_clock_reading_is_read_whole_where_the_token_rule_truncates_it():
    """The one partial read the token rule allows, undone by delimiting the value.

    Unquoted, `@occurred:2026-06-10 10:00 AM` files a calendar date and leaves the clock
    reading in the content (pinned above). Quoted, the same text files the instant the
    author meant, and nothing is left behind.
    """
    observation = _observation('- [decision] @occurred:"2026-06-10 10:00 AM" The cutover ran.')

    [assertion] = observation.temporal
    assert str(assertion.valid_during) == "[2026-06-10T10:00:00.000000Z,)"
    assert assertion.valid_during.axis is TemporalRangeAxis.INSTANT
    assert observation.content == "The cutover ran."


def test_the_closing_quote_ends_the_token_and_the_rest_stays_content():
    """Content after the closing quote is ordinary content, quotes and digits included.

    Whitespace no longer delimits the token, so the peel has to stop at the quote and
    hand back everything after it exactly as written -- including text that would have
    been read as more date had the scan kept going.
    """
    line = (
        '- [decision] @occurred:"June 10, 2026" She said "go", then 10, 2026 '
        "shipped #infra (agreed)"
    )

    observation = _observation(line)

    [assertion] = observation.temporal
    assert assertion.source_text == '@occurred:"June 10, 2026"'
    assert observation.content == 'She said "go", then 10, 2026 shipped #infra'
    assert observation.tags == ["infra"]
    assert observation.context == "agreed"
    assert str(observation) == line


def test_content_may_follow_the_closing_quote_with_no_space():
    """The quote is the boundary, so nothing else has to mark it."""
    observation = _observation('- [decision] @occurred:"June 10, 2026"The cutover ran.')

    [assertion] = observation.temporal
    assert assertion.source_text == '@occurred:"June 10, 2026"'
    assert observation.content == "The cutover ran."


@pytest.mark.parametrize(
    "qualifier",
    ['@occurred:""', '@occurred:"not a date"', '@"the cutover week"'],
)
def test_a_quoted_payload_that_is_not_a_date_stays_content_silently(qualifier: str):
    """Quoting says where the value ends, not that the value is a date."""
    observation = _observation(f"- [decision] {qualifier} The cutover ran.")

    assert observation.temporal == []
    assert observation.temporal_error is None
    assert observation.content == f"{qualifier} The cutover ran."


def test_a_quoted_point_still_reports_an_unknown_kind():
    """The quoted form is a spelling of the point, so it keeps the point's diagnostic."""
    observation = _refusal('- [decision] @asserted:"June 10, 2026" The cutover ran.')

    assert "unknown temporal kind 'asserted'" in (observation.temporal_error or "")
    assert observation.content.startswith('@asserted:"June 10, 2026"')


def test_an_unterminated_quote_is_reported_instead_of_swallowing_the_line():
    """Reading on would hand the author's prose to the date reader; refusing keeps it."""
    line = '- [decision] @occurred:"June 10, 2026 The cutover ran.'

    observation = _refusal(line)

    assert "unterminated quote" in (observation.temporal_error or "")
    # The fix is shown, not described.
    assert '@occurred:"June 10, 2026"' in (observation.temporal_error or "")
    assert observation.content == '@occurred:"June 10, 2026 The cutover ran.'
    assert str(observation) == line


def test_an_escaped_quote_belongs_to_the_value_and_cannot_close_it():
    r"""`\"` is part of the date text, which is why this line has no closing quote left."""
    observation = _refusal('- [decision] @occurred:"June 10, 2026\\" The cutover ran.')

    assert "unterminated quote" in (observation.temporal_error or "")
    assert observation.content == '@occurred:"June 10, 2026\\" The cutover ran.'


# --- The truncation diagnostic: when the one-token rule costs a date ---


def test_a_truncated_date_names_the_quoted_form_as_the_fix():
    """`@occurred:June 10, 2026` is the shape quoting exists for, so say so once."""
    observation = _refusal("- [decision] @occurred:June 10, 2026 The cutover ran.")

    error = observation.temporal_error or ""
    assert "'@occurred:June'" in error
    assert "names only a month or a year" in error
    assert '@occurred:"June 10, 2026"' in error
    # Reported, never half-read: the line is still exactly what the author wrote.
    assert observation.content == "@occurred:June 10, 2026 The cutover ran."


def test_a_too_short_number_followed_by_a_digit_names_the_quoted_form_too():
    """The other guard gets the same treatment, with its own reason and the same fix."""
    observation = _refusal("- [note] @12 2026 was the year of the cutover.")

    error = observation.temporal_error or ""
    assert "'@12'" in error
    assert "is narrower than a year" in error
    # A point with no kind is fixed by the quoted form with no kind.
    assert '@"June 10, 2026"' in error


@pytest.mark.parametrize(
    "line",
    [
        # Prose follows, so nothing suggests a date was cut short.
        "- [decision] @occurred:June the cat sat on the mat",
        "- [decision] @occurred:may The cutover ran.",
        "- [decision] @1 shipped the cutover.",
        # Nothing follows at all.
        "- [decision] @occurred:June",
        # A digit follows, but `@vol:` is an ordinary `@word:` marker, not a kind.
        "- [note] @vol:2 3 pages of notes",
    ],
)
def test_a_refused_point_stays_silent_when_the_line_did_not_continue_the_date(line: str):
    """Today's behavior, kept: the diagnostic fires on one signal, not on every refusal."""
    observation = _observation(line)

    assert observation.temporal == []
    assert observation.temporal_error is None


@pytest.mark.parametrize(
    ("date_order", "expected_lower"),
    [("YMD", "2026-04-03"), ("DMY", "2026-04-03"), ("MDY", "2026-03-04")],
)
def test_a_slash_date_with_a_kind_follows_the_configured_order(
    date_order: DateOrder, expected_lower: str
):
    """`@occurred:03/04/2026` resolves by preference, through the real parse path."""
    observation = parse_temporal_qualifier(
        "@occurred:03/04/2026 The cutover ran.", date_order=date_order
    )

    [assertion] = observation.assertions
    assert assertion.valid_during.lower == expected_lower
    assert observation.content == "The cutover ran."


# --- Date order comes from configuration ---


def test_configured_date_order_decides_an_ambiguous_slash_date(monkeypatch):
    """`@10/07/2026` is July 10 by default and October 7 under MDY."""
    default = _observation("- [decision] @10/07/2026 The cutover ran.")
    [assertion] = default.temporal
    assert assertion.valid_during.lower == "2026-07-10"

    monkeypatch.setenv("BASIC_MEMORY_DATE_ORDER", "MDY")
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    assert ConfigManager().config.date_order == "MDY"

    reordered = _observation("- [decision] @10/07/2026 The cutover ran.")
    [assertion] = reordered.temporal
    assert assertion.valid_during.lower == "2026-10-07"


def test_configured_date_order_never_reinterprets_an_iso_date(monkeypatch):
    """An ISO date is unambiguous, so the preference must not touch it."""
    monkeypatch.setenv("BASIC_MEMORY_DATE_ORDER", "MDY")
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)

    observation = _observation("- [decision] @2026-07-10 The cutover ran.")

    [assertion] = observation.temporal
    assert assertion.valid_during.lower == "2026-07-10"


# --- The unknown-kind diagnostic ---


def test_unknown_kind_in_a_range_literal_reports_diagnostic_and_keeps_text():
    """`@asserted` is well-formed but names no kind this system understands."""
    observation = _refusal("- [decision] @asserted[2026-06-10,) The cache layer will use Redis.")

    assert "unknown temporal kind 'asserted'" in (observation.temporal_error or "")
    # The diagnostic names the kinds that would have worked.
    assert "effective" in (observation.temporal_error or "")
    # Never silently dropped: the text is still searchable content.
    assert observation.content.startswith("@asserted[2026-06-10,)")


def test_unknown_kind_in_a_point_reports_diagnostic_and_keeps_text():
    """The payload reads as a date, so the author is plainly naming a kind."""
    observation = _refusal("- [decision] @asserted:2026-06-10 The cache layer will use Redis.")

    assert "unknown temporal kind 'asserted'" in (observation.temporal_error or "")
    assert observation.content.startswith("@asserted:2026-06-10")


def test_an_unknown_kind_with_an_unreadable_payload_is_left_alone():
    """`@todo:fix the thing` is prose, not a broken qualifier.

    The diagnostic is reserved for a payload that actually reads as time; without that,
    reporting would fire on ordinary `@word:` markers.
    """
    observation = _observation("- [decision] @todo:fix the cache layer")

    assert observation.temporal == []
    assert observation.temporal_error is None
    assert observation.content.startswith("@todo:fix")


# --- Everything else is content, silently ---


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        # A known kind glued to something that is not a range literal.
        ("- [decision] @effective[2026-06-10 Use Redis.", "@effective[2026-06-10"),
        # A range mixing the two axes.
        ("- [decision] @effective[2026-06-10,2026-07-27T00:00:00Z) Use Redis.", "@effective["),
        # A range that ends before it begins.
        ("- [decision] @effective[2026-08-01,2026-06-10) Use Redis.", "@effective["),
        # A date that the calendar does not have.
        ("- [decision] @effective[2026-02-30,) Use Redis.", "@effective[2026-02-30,)"),
        ("- [decision] @2026-02-30 Use Redis.", "@2026-02-30"),
        # A moment that leaves the calendar once it is shifted to UTC.
        ("- [decision] @effective[9999-12-31T23:59:59-05:00,) Use Redis.", "@effective["),
        ("- [decision] @effective:9999-12-31T23:59:59-05:00 Use Redis.", "@effective:"),
        # Trailing junk: one broken token, not a qualifier plus content.
        ("- [decision] @effective[2026-06-10,2026-07-27)x Use Redis.", "@effective["),
    ],
)
def test_a_payload_that_does_not_read_as_time_stays_content(line: str, kept: str):
    """No warning about how someone wrote a date -- the token is simply not a qualifier."""
    observation = _observation(line)

    assert observation.temporal == []
    assert observation.temporal_error is None
    assert observation.content.startswith(kept)


# --- One qualifier never costs the note its index ---


def test_a_qualifier_at_the_end_of_the_calendar_does_not_fail_the_note():
    """Whatever a qualifier says, the rest of the note still parses.

    `@effective:9999-12` used to build `date(10000, 1, 1)`; the `ValueError` escaped
    `parse_authored_point` and `parse_temporal_qualifier` -- neither of which guards that
    call -- into the markdown parser, so *the whole document* failed over one qualifier:
    every other observation and relation on the page went with it. December 9999 is
    representable as `[9999-12-01,)`, so it files like any other period, and the
    instant beside it, which is not representable at all, is simply left as content.
    """
    content = "\n".join(
        [
            "## Observations",
            "- [decision] @effective:9999-12 The cache layer will use Redis.",
            "- [decision] @effective:9999 The contract holds all year.",
            "- [decision] @effective[9999-12-31T23:59:59-05:00,) An unstorable moment.",
            "- [note] An ordinary observation that must still index.",
            "",
            "## Relations",
            "- relates_to [[Cache Layer]]",
        ]
    )

    parsed = parse(content)

    month, year, unstorable, ordinary = parsed.observations
    [month_assertion] = month.temporal
    [year_assertion] = year.temporal
    assert str(month_assertion.valid_during) == "[9999-12-01,)"
    assert str(year_assertion.valid_during) == "[9999-01-01,)"
    assert month.content == "The cache layer will use Redis."
    assert year.content == "The contract holds all year."
    # Unreadable, so never peeled: the line keeps its exact text and reports nothing.
    assert unstorable.temporal == []
    assert unstorable.temporal_error is None
    assert unstorable.content == "@effective[9999-12-31T23:59:59-05:00,) An unstorable moment."
    # The rest of the note is what the crash used to take with it.
    assert ordinary.content == "An ordinary observation that must still index."
    assert [relation.target for relation in parsed.relations] == ["Cache Layer"]


def test_qualifier_with_nothing_to_qualify_stays_content():
    """Peeling it would leave an empty observation, which the plugin drops outright."""
    observation = _observation("- [decision] @effective[2026-06-10,2026-07-27)")

    assert observation.temporal == []
    assert observation.temporal_error is None
    assert observation.content == "@effective[2026-06-10,2026-07-27)"


@pytest.mark.parametrize(
    "line",
    [
        "- [note] Contact paul@basicmemory.com about the cutover",
        "- [note] Ping @paul before the cutover",
        "- [note] @basicmemory.com is great",
        "- [note] @someone(2026) filed the ticket",
        "- [note] Email me at ops@example.com (urgent)",
        "- [note] @ops@example.com owns the runbook",
        "- [note] @paul reviewed the cutover",
    ],
)
def test_non_qualifier_at_tokens_are_ordinary_content(line: str):
    """`@` is common prose, and none of it may become a valid-time assertion."""
    observation = _observation(line)

    assert observation.temporal == []
    assert observation.temporal_error is None


# --- Acceptance 9 and 10: the two axes never convert into one another ---


def test_date_only_bounds_never_acquire_time_or_zone():
    """Acceptance 9: a calendar date stays a calendar date, with no false precision."""
    observation = _observation("- [decision] @effective[2026-06-10,2026-07-27) Use Redis.")

    [assertion] = observation.temporal
    assert assertion.valid_during.axis is TemporalRangeAxis.DATE
    assert assertion.valid_during.lower == "2026-06-10"
    assert assertion.valid_during.upper == "2026-07-27"
    assert "T" not in (assertion.valid_during.lower or "")
    assert "Z" not in (assertion.valid_during.upper or "")


def test_a_date_point_never_becomes_midnight_utc():
    """The point form must not promote a date onto the instant axis either.

    Midnight in *which* zone is a question the author never answered, and answering it
    for them would make a date query and an instant query disagree about this note.
    """
    observation = _observation("- [decision] @effective:2026-06-10 Use Redis.")

    [assertion] = observation.temporal
    assert assertion.valid_during.axis is TemporalRangeAxis.DATE
    assert assertion.valid_during.lower == "2026-06-10"
    assert "T00:00" not in str(assertion.valid_during)


def test_naive_timestamp_bounds_are_read_as_utc():
    """A timestamp with no offset is UTC, not a refusal.

    Both spellings of the same moment must produce the same stored bound, or a search
    would answer differently depending on how the author punctuated it.
    """
    naive = _observation("- [decision] @occurred[2026-07-27T18:42:00,) Cutover ran.")
    explicit = _observation("- [decision] @occurred[2026-07-27T18:42:00Z,) Cutover ran.")

    [from_naive] = naive.temporal
    [from_explicit] = explicit.temporal
    assert naive.temporal_error is None
    assert from_naive.valid_during == from_explicit.valid_during
    assert from_naive.valid_during.lower == "2026-07-27T18:42:00.000000Z"


def test_instant_bounds_normalize_to_utc():
    """An offset bound names an instant, and is stored as that instant in UTC."""
    observation = _observation(
        "- [decision] @occurred[2026-07-27T18:42:00+02:00,2026-07-28T00:00:00Z) Cutover ran."
    )

    [assertion] = observation.temporal
    assert assertion.valid_during.lower == "2026-07-27T16:42:00.000000Z"
    # The author's own text is what round-trips, offset and all.
    assert assertion.source_text.startswith("@occurred[2026-07-27T18:42:00+02:00")


# --- Direct scanner contract ---


def test_scanner_returns_content_unchanged_when_nothing_is_attempted():
    """The scanner is a peel, not a rewrite: untouched content is returned as-is."""
    result = parse_temporal_qualifier("Plain observation content")

    assert result.content == "Plain observation content"
    assert result.assertions == ()
    assert result.error is None


def test_scanner_takes_an_explicit_date_order():
    """A caller that already holds the config passes it instead of re-reading it."""
    result = parse_temporal_qualifier("@10/07/2026 The cutover ran.", date_order="MDY")

    [assertion] = result.assertions
    assert assertion.valid_during.lower == "2026-10-07"
    assert result.content == "The cutover ran."
