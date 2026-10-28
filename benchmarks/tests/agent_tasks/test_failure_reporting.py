"""Provider errors cannot become a fabricated zero-accuracy result."""

from basic_memory_benchmarks.agent_tasks.driver import build_surface_summary
from basic_memory_benchmarks.agent_tasks.models import AgentTaskResult


def test_only_errored_tasks_have_no_pass_rate_and_keep_partial_usage():
    summary = build_surface_summary(
        "bm",
        "model",
        [
            AgentTaskResult(
                surface="bm",
                task_id="task",
                skill="memory",
                passed=False,
                error="provider credits exhausted",
                total_input_tokens=10,
            )
        ],
    )
    assert summary.tasks_errored == summary.tasks_total == 1
    assert summary.pass_rate is None
    assert summary.tokens_per_completed_task is None
    assert summary.total_input_tokens == 10
