from unittest.mock import AsyncMock, MagicMock

import pytest
from tau import extension
from tau.bridge import Settings
from tau.continuity import NAMESPACE, CaptureRecord, digest
from tau_agent.session import CustomEntry
from tau_coding.extensions import ExtensionAPI
from test_recovery import boundary


@pytest.mark.parametrize("fails", [True, False])
async def test_start_reconciles_pending_intents_without_replaying_messages(fails: bool) -> None:
    lifecycle, context, api = boundary()
    lifecycle.settings = Settings(project="notes", auto_recall=False)
    record = CaptureRecord(
        project="notes",
        capture_id="pending",
        kind="transcript",
        status="pending",
        source_tip="tip",
        content_digest=digest("body"),
    )
    context.branch_entries.append(
        CustomEntry(namespace=NAMESPACE, data=record.model_dump(mode="json"))
    )
    lifecycle.connection.start = AsyncMock()
    lifecycle.persist = (
        AsyncMock(side_effect=RuntimeError("not accepted"))
        if fails
        else AsyncMock(return_value="note.md")
    )
    await lifecycle.start(None, context)
    lifecycle.persist.assert_awaited_once()
    assert bool(lifecycle.last_error) is fails
    api.append_message.assert_not_called()


def test_stock_tau_fails_with_actionable_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(ExtensionAPI, "append_message")
    with pytest.raises(RuntimeError, match="requires Tau PR #683"):
        extension.setup(MagicMock(spec=ExtensionAPI))
