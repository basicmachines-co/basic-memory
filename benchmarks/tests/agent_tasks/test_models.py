"""AgentTasksConfig field rules: values that cannot survive a run are rejected.

Both rules live on the model rather than only in the CLI because
``run_agent_tasks(config)`` is an explicitly supported entrypoint, and each bad
value fails late and illegibly once a run is underway.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from basic_memory_benchmarks.agent_tasks.models import AgentTasksConfig

NON_FINITE = ["nan", "inf", "-inf"]


def _config(**overrides: Any) -> AgentTasksConfig:
    defaults: dict[str, Any] = {
        "run_id": "at-abc123",
        "model_spec": "scripted:inline",
        "bm_local_path": "/tmp/bm-checkout",
    }
    defaults.update(overrides)
    return AgentTasksConfig(**defaults)


@pytest.mark.parametrize("raw", NON_FINITE)
def test_rejects_non_finite_model_temperature(raw: str) -> None:
    with pytest.raises(ValidationError, match="finite"):
        _config(model_temperature=float(raw))


def test_finite_temperatures_and_the_omit_sentinel_still_pass() -> None:
    assert _config(model_temperature=0.0).model_temperature == 0.0
    assert _config(model_temperature=0.7).model_temperature == 0.7
    # None is the documented "omit the parameter entirely" sentinel.
    assert _config(model_temperature=None).model_temperature is None


def test_non_finite_temperature_can_no_longer_be_recorded_as_omitted() -> None:
    """The substantive half: a corrupted run record is worse than a crash.

    JSON has no nan/inf spelling, so Pydantic serializes both as null — which is
    exactly this field's "temperature omitted" sentinel. Before the guard, a nan
    run wrote a manifest.json claiming no temperature was sent, and the run still
    looked valid afterwards.
    """
    for raw in NON_FINITE:
        with pytest.raises(ValidationError):
            _config(model_temperature=float(raw))

    # A recorded run config still round-trips the real value, including the
    # sentinel — the guard rejects, it does not rewrite.
    assert '"model_temperature":0.7' in _config(model_temperature=0.7).model_dump_json()
    assert '"model_temperature":null' in _config(model_temperature=None).model_dump_json()


def test_unguarded_float_field_really_does_collapse_to_null() -> None:
    """Pins the reason the guard exists so its rationale cannot silently rot."""

    class Unguarded(BaseModel):
        model_temperature: float | None = 0.0

    for raw in NON_FINITE:
        dumped = Unguarded(model_temperature=float(raw)).model_dump_json()
        assert dumped == '{"model_temperature":null}'


@pytest.mark.parametrize(
    "run_id",
    [
        "-trial",  # parsed as options by `bm project add`: "No such option: -t"
        "--run",
        "",
        ".hidden",
        "..",
        "nested/run",
        "back\\slash",
        "has space",
    ],
)
def test_rejects_run_ids_that_are_unsafe_as_argv_or_path(run_id: str) -> None:
    with pytest.raises(ValidationError, match="run_id must start with"):
        _config(run_id=run_id)


@pytest.mark.parametrize("run_id", ["at-abc123", "test-run", "run_2026.09.01", "A1", "_local"])
def test_accepts_ordinary_run_ids(run_id: str) -> None:
    assert _config(run_id=run_id).run_id == run_id


def test_accepted_run_ids_generate_option_safe_project_names() -> None:
    """The invariant the rule exists to protect.

    The driver builds project names as ``{run_id}-{task.id}`` and hands them
    straight to ``bm project add``; a name starting with "-" is parsed as options
    and aborts the run after the corpus copy, with stderr captured so the
    operator sees only a bare CalledProcessError exit status.
    """
    for run_id in ("at-abc123", "test-run", "_local"):
        project_name = f"{_config(run_id=run_id).run_id}-curate-orphans"
        assert not project_name.startswith("-")
