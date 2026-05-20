"""Tests for background task done-callback error handling."""

import asyncio
from unittest.mock import patch

import pytest

from basic_memory.deps.services import _log_task_failure


@pytest.mark.asyncio
async def test_log_task_failure_ignores_cancelled_task():
    async def slow():
        await asyncio.sleep(10)

    task = asyncio.create_task(slow())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with patch("basic_memory.deps.services.logger.exception") as mock_exc:
        _log_task_failure(task)
        mock_exc.assert_not_called()


@pytest.mark.asyncio
async def test_log_task_failure_logs_real_exception():
    async def boom():
        raise ValueError("sync failed")

    task = asyncio.create_task(boom())
    with pytest.raises(ValueError):
        await task

    with patch("basic_memory.deps.services.logger.exception") as mock_exc:
        _log_task_failure(task)
        mock_exc.assert_called_once()
        assert "sync failed" in str(mock_exc.call_args)
