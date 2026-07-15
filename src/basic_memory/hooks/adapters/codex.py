"""Codex hook stdin adapter.

Ground truth for the payload shape: the original Codex hook scripts (replaced
by zero-logic shims in plugins/codex/hooks/; the recorded fixtures in
tests/hooks/fixtures/ preserve the shapes), which read these fields from
stdin JSON:

  session-start: cwd, source (startup|resume|compact), session_id,
                 transcript_path
  pre-compact:   cwd, transcript_path, session_id, turn_id,
                 trigger (manual|auto), model
"""

from __future__ import annotations

from basic_memory.hooks.adapters.base import HarnessAdapter, HookPayload, NormalizedHookEvent

SOURCE = "codex"


def normalize(event: str, payload: HookPayload) -> NormalizedHookEvent:
    """Normalize a Codex hook payload into the shared event shape."""
    trigger = payload.get("trigger") or payload.get("source")
    turn_id = payload.get("turn_id")
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
