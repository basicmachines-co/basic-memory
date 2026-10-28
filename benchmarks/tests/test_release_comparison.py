"""Pair ordering preserves equal repeats and reverses which product goes first."""

from basic_memory_benchmarks.release_comparison import paired_order


def test_six_repeats_are_balanced_alternating_pairs():
    order = paired_order(6)
    assert len(order) == 12
    for pair in range(1, 7):
        labels = [label for number, label in order if number == pair]
        assert labels == (["baseline", "candidate"] if pair % 2 else ["candidate", "baseline"])
