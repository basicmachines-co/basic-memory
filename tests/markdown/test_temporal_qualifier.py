"""Parsing and round-tripping SPEC-82 temporal qualifiers on observations.

Three rules shape every test here:

* **One grammar.** `@[role]<range-literal>` for a precise interval,
  `@[role:]<date>` for a point. The role is optional in both; a role-less point must
  begin with a digit.
* **Silent when it is not time.** If the payload does not read as a date, the token is
  ordinary content and nothing is reported. Prose is full of `@`, and diagnosing every
  one of them would be noise.
* **One diagnostic.** A payload that *does* read as time but names an unknown role is
  reported, because a short list of valid roles makes that actionable.

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
from basic_memory.temporal import TemporalRangeKind, TimeRole


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


# --- Acceptance 2: round trip preserves role and bounds ---


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
        # The point: the convenient form, with and without a role.
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


def test_qualifier_carries_its_role_and_bounds():
    """The parsed assertion is the interval the author wrote, on the axis they named."""
    observation = _observation(
        "- [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis."
    )

    [assertion] = observation.temporal
    assert assertion.time_role is TimeRole.EFFECTIVE
    assert assertion.valid_during.kind is TemporalRangeKind.DATE
    assert assertion.valid_during.lower == "2026-06-10"
    assert assertion.valid_during.upper == "2026-07-27"
    assert assertion.valid_during.lower_inclusive is True
    assert assertion.valid_during.upper_inclusive is False
    assert str(assertion.valid_during) == "[2026-06-10,2026-07-27)"


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
    ("qualifier", "literal", "kind"),
    [
        # A year and a month are periods the author delimited by writing them.
        ("@2026", "[2026-01-01,2027-01-01)", TemporalRangeKind.DATE),
        ("@2026-06", "[2026-06-01,2026-07-01)", TemporalRangeKind.DATE),
        # A date says when something started and leaves it open.
        ("@2026-06-10", "[2026-06-10,)", TemporalRangeKind.DATE),
        # So does a moment, on the instant axis.
        (
            "@2026-06-10T14:00:00",
            "[2026-06-10T14:00:00.000000Z,)",
            TemporalRangeKind.INSTANT,
        ),
        (
            "@2026-06-10T14:00:00Z",
            "[2026-06-10T14:00:00.000000Z,)",
            TemporalRangeKind.INSTANT,
        ),
        (
            "@2026-06-10T14:00:00+02:00",
            "[2026-06-10T12:00:00.000000Z,)",
            TemporalRangeKind.INSTANT,
        ),
    ],
)
def test_point_qualifier_canonicalizes_to_the_span_its_precision_covers(
    qualifier: str, literal: str, kind: TemporalRangeKind
):
    observation = _observation(f"- [decision] {qualifier} The cutover ran.")

    [assertion] = observation.temporal
    assert str(assertion.valid_during) == literal
    assert assertion.valid_during.kind is kind


def test_role_less_point_is_filed_on_the_valid_axis():
    """`@2026-06-10` says when the statement holds, without narrowing how."""
    observation = _observation("- [decision] @2026-06-10 The cache layer will use Redis.")

    [assertion] = observation.temporal
    assert assertion.time_role is TimeRole.VALID


def test_role_less_range_literal_is_filed_on_the_valid_axis():
    """The role is optional in both forms, and defaults the same way in both."""
    observation = _observation("- [decision] @[2026-06-10,2026-07-27) Use Redis.")

    [assertion] = observation.temporal
    assert assertion.time_role is TimeRole.VALID
    assert str(assertion.valid_during) == "[2026-06-10,2026-07-27)"


@pytest.mark.parametrize(
    ("qualifier", "role"),
    [
        ("@effective:2026-06-10", TimeRole.EFFECTIVE),
        ("@occurred:2026-06-10", TimeRole.OCCURRED),
        ("@due:2026-06-10", TimeRole.DUE),
        ("@mentioned:2026-06-10", TimeRole.MENTIONED),
        ("@valid:2026-06-10", TimeRole.VALID),
    ],
)
def test_point_qualifier_names_its_axis_with_a_colon(qualifier: str, role: TimeRole):
    """`:` separates role from date; a date can start with a letter, so it is needed."""
    observation = _observation(f"- [decision] {qualifier} The cutover ran.")

    [assertion] = observation.temporal
    assert assertion.time_role is role
    assert str(assertion.valid_during) == "[2026-06-10,)"


def test_a_roled_point_accepts_a_relative_date():
    """With a role the author has said what they mean, so any readable date is taken.

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
def test_a_role_less_point_must_be_digit_led_and_year_wide(qualifier: str):
    """A bare `@token` that short is a mention, a version, or a list marker.

    Accepting what dateparser makes of these would silently file wrong valid time on
    ordinary prose. An author who really means one writes the role: `@occurred:may`.
    """
    observation = _observation(f"- [decision] {qualifier} shipped the cutover.")

    assert observation.temporal == []
    assert observation.temporal_error is None
    assert observation.content.startswith(qualifier)


def test_a_short_point_is_still_accepted_when_the_role_is_named():
    """The width rule guards the *bare* form only; a role removes the ambiguity."""
    observation = _observation("- [decision] @occurred:may The cutover ran.")

    [assertion] = observation.temporal
    assert assertion.time_role is TimeRole.OCCURRED
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


# --- The one diagnostic: an unknown role ---


def _refusal(line: str) -> Observation:
    """Parse a line whose qualifier must be refused, and assert the shared contract."""
    observation = _observation(line)
    assert observation.temporal == []
    assert observation.temporal_error is not None
    return observation


def test_unknown_role_in_a_range_literal_reports_diagnostic_and_keeps_text():
    """`@asserted` is well-formed but names no axis this system understands."""
    observation = _refusal("- [decision] @asserted[2026-06-10,) The cache layer will use Redis.")

    assert "unknown temporal role 'asserted'" in (observation.temporal_error or "")
    # The diagnostic names the roles that would have worked.
    assert "effective" in (observation.temporal_error or "")
    # Never silently dropped: the text is still searchable content.
    assert observation.content.startswith("@asserted[2026-06-10,)")


def test_unknown_role_in_a_point_reports_diagnostic_and_keeps_text():
    """The payload reads as a date, so the author is plainly naming an axis."""
    observation = _refusal("- [decision] @asserted:2026-06-10 The cache layer will use Redis.")

    assert "unknown temporal role 'asserted'" in (observation.temporal_error or "")
    assert observation.content.startswith("@asserted:2026-06-10")


def test_an_unknown_role_with_an_unreadable_payload_is_left_alone():
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
        # A known role glued to something that is not a range literal.
        ("- [decision] @effective[2026-06-10 Use Redis.", "@effective[2026-06-10"),
        # A range mixing the two axes.
        ("- [decision] @effective[2026-06-10,2026-07-27T00:00:00Z) Use Redis.", "@effective["),
        # A range that ends before it begins.
        ("- [decision] @effective[2026-08-01,2026-06-10) Use Redis.", "@effective["),
        # A date that the calendar does not have.
        ("- [decision] @effective[2026-02-30,) Use Redis.", "@effective[2026-02-30,)"),
        ("- [decision] @2026-02-30 Use Redis.", "@2026-02-30"),
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
    assert assertion.valid_during.kind is TemporalRangeKind.DATE
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
    assert assertion.valid_during.kind is TemporalRangeKind.DATE
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
