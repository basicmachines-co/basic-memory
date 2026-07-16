"""Unit tests for the RFC 9562 UUIDv7 generator."""

import time
import uuid

import pytest

from basic_memory.hooks import _uuid7
from basic_memory.hooks._uuid7 import uuid7, uuid7_unix_ms


def test_uuid7_sets_version_and_variant_bits() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_embeds_current_unix_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_ns = 1_752_580_800_123 * 1_000_000  # a fixed wall clock, ms precision
    monkeypatch.setattr(_uuid7.time, "time_ns", lambda: fixed_ns)

    value = uuid7()

    assert uuid7_unix_ms(value) == 1_752_580_800_123


def test_uuid7_strings_sort_chronologically(monkeypatch: pytest.MonkeyPatch) -> None:
    # The inbox relies on filename order == capture order; force distinct
    # milliseconds and check the string sort matches time order.
    base_ns = time.time_ns()
    values: list[str] = []
    for offset_ms in (0, 1, 5, 250, 60_000):
        monkeypatch.setattr(_uuid7.time, "time_ns", lambda ns=base_ns + offset_ms * 1_000_000: ns)
        values.append(str(uuid7()))

    assert values == sorted(values)


def test_uuid7_random_bits_differ_within_same_millisecond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_ns = time.time_ns()
    monkeypatch.setattr(_uuid7.time, "time_ns", lambda: fixed_ns)

    assert uuid7() != uuid7()


def test_uuid7_unix_ms_rejects_non_v7_uuid() -> None:
    """Only v7 carries a timestamp; shifting a v4 would fabricate a capture time."""
    with pytest.raises(ValueError):
        uuid7_unix_ms(uuid.uuid4())


def test_uuid7_unix_ms_roundtrips_a_generated_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_ms = 1_752_580_800_123
    monkeypatch.setattr(_uuid7.time, "time_ns", lambda: fixed_ms * 1_000_000)

    assert uuid7_unix_ms(uuid7()) == fixed_ms
