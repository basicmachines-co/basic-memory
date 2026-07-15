"""Claude Code hook stdin adapter.

Ground truth for the payload shape: the official hooks reference plus the
shipped hook scripts (plugins/claude-code/hooks/*.sh), which parse the same
fields. Claude Code sends one JSON object on stdin:

  SessionStart: session_id, transcript_path, cwd, hook_event_name,
                source (startup|resume|clear|compact)
  PreCompact:   session_id, transcript_path, cwd, hook_event_name,
                trigger (manual|auto), custom_instructions

Claude hooks carry no turn identifier and no model field.
"""

from __future__ import annotations

from basic_memory.hooks.adapters.base import HarnessAdapter, HookPayload, NormalizedHookEvent

SOURCE = "claude-code"


def normalize(event: str, payload: HookPayload) -> NormalizedHookEvent:
    """Normalize a Claude Code hook payload into the shared event shape."""
    # SessionStart reports its cause as `source`, PreCompact as `trigger`;
    # both collapse into the normalized trigger slot.
    trigger = payload.get("trigger") or payload.get("source")
    return NormalizedHookEvent(
        source=SOURCE,
        event=event,
        session_id=str(payload.get("session_id") or ""),
        turn_id=None,
        cwd=str(payload.get("cwd") or ""),
        transcript_path=str(payload.get("transcript_path") or ""),
        trigger=str(trigger) if trigger else None,
        model=None,
    )


ADAPTER = HarnessAdapter(source=SOURCE, normalize=normalize)
