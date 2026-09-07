"""Pi hook stdin adapter.

Pi does not have a built-in hook-file installer in Basic Memory. This adapter
normalizes payloads emitted by the Pi extension when it chooses to delegate a
lifecycle action to ``bm hook`` for experimentation:

  session-start: cwd, source/trigger, session_id, transcript_path, model
  pre-compact:   cwd, trigger, session_id, branch_id/turn_id, transcript_path, model
"""

from __future__ import annotations

from basic_memory.hooks.adapters.base import HarnessAdapter, HookPayload, NormalizedHookEvent

SOURCE = "pi"


def normalize(event: str, payload: HookPayload) -> NormalizedHookEvent:
    """Normalize a Pi extension hook payload into the shared event shape."""
    trigger = payload.get("trigger") or payload.get("source")
    turn_id = payload.get("turn_id") or payload.get("branch_id")
    model = payload.get("model")
    return NormalizedHookEvent(
        source=SOURCE,
        event=event,
        session_id=str(payload.get("session_id") or ""),
        turn_id=str(turn_id) if turn_id else None,
        cwd=str(payload.get("cwd") or ""),
        transcript_path=str(payload.get("transcript_path") or ""),
        trigger=str(trigger) if trigger else None,
        model=str(model) if model else None,
    )


ADAPTER = HarnessAdapter(source=SOURCE, normalize=normalize)
