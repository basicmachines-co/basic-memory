"""ProjectScope: the explicit project set every search statement binds."""

from typing import Any, cast

import pytest

from basic_memory.repository.search_scope import ProjectScope


def test_of_sorts_and_dedupes() -> None:
    assert ProjectScope.of([3, 1, 3, 2]).project_ids == (1, 2, 3)
    assert ProjectScope.of([3, 1, 3, 2]) == ProjectScope.of((1, 2, 3))


def test_single_and_empty() -> None:
    assert ProjectScope.single(7).project_ids == (7,)
    assert not ProjectScope.single(7).is_empty
    assert ProjectScope.of([]).is_empty


@pytest.mark.parametrize("bad", [0, -1, True])
def test_rejects_non_positive_ids(bad: int) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        ProjectScope.of([bad])


def test_rejects_non_int_ids() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        ProjectScope.of(cast("list[int]", ["1"]))


def test_predicate_binds_each_id_once_per_statement() -> None:
    params: dict[str, Any] = {}
    scope = ProjectScope.of([5, 2])
    assert (
        scope.predicate("search_index.project_id", params)
        == "search_index.project_id IN (:scope_0, :scope_1)"
    )
    # A second reference within the same statement reuses the binds.
    assert scope.predicate("owner.project_id", params) == "owner.project_id IN (:scope_0, :scope_1)"
    assert params == {"scope_0": 2, "scope_1": 5}


def test_empty_scope_matches_nothing_and_binds_nothing() -> None:
    params: dict[str, Any] = {}
    assert ProjectScope.of([]).predicate("search_index.project_id", params) == "1 = 0"
    assert params == {}
