"""Turn-scoped evidence for correcting unsupported Basic Memory save claims."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, replace


_SAVE_CLAIM = re.compile(
    r"(?:^|[.!?]\s+)(?:I(?:['’]ve| have)?\s+)?"
    r"(?:saved|stored|recorded|remembered|added|updated)\b",
    re.IGNORECASE,
)
_MEMORY_REQUEST = re.compile(r"\bremember\b", re.IGNORECASE)
_MEMORY_NAME = re.compile(r"\bbasic[- ]memory\b", re.IGNORECASE)
_CORRECTION = (
    "Basic Memory verification: no successful Basic Memory write was observed in this turn. "
    "The save claim above is unverified."
)


def claims_memory_save(response: str, *, memory_requested: bool) -> bool:
    """Recognize direct English save confirmations outside quotes and code blocks."""
    in_code = False
    for line in response.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if in_code or stripped.startswith((">", '"', "'")):
            continue
        plain = stripped.replace("**", "").replace("__", "")
        if _SAVE_CLAIM.search(plain) and (memory_requested or _MEMORY_NAME.search(plain)):
            return True
    return False


@dataclass(frozen=True)
class TurnEvidence:
    turn_id: str
    memory_requested: bool
    write_observed: bool = False


class SaveClaimGuard:
    """Correlate observer hooks without borrowing writes from other turns/sessions.

    Hermes serializes turns within a session. The explicit turn ID on tool
    events also rejects late completion events from a prior interrupted turn.
    """

    def __init__(self) -> None:
        self._turns: dict[str, TurnEvidence] = {}
        self._lock = threading.Lock()

    def begin_turn(
        self, *, session_id: str = "", turn_id: str = "", user_message: str = "", **_: object
    ) -> None:
        if not session_id or not turn_id:
            return
        # Generic save requests also cover images and files outside Basic Memory.
        # Only an explicit memory destination or remember request supplies context
        # for a destination-free confirmation such as "I've saved it."
        requested = bool(_MEMORY_REQUEST.search(user_message) or _MEMORY_NAME.search(user_message))
        with self._lock:
            self._turns[session_id] = TurnEvidence(turn_id, requested)

    def observe_tool(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        tool_name: str = "",
        status: str = "",
        **_: object,
    ) -> None:
        if tool_name not in {"bm_write", "bm_edit"} or status != "ok":
            return
        with self._lock:
            current = self._turns.get(session_id)
            if current is not None and current.turn_id == turn_id:
                self._turns[session_id] = replace(current, write_observed=True)

    def transform(
        self, *, session_id: str = "", response_text: str = "", **_: object
    ) -> str | None:
        with self._lock:
            current = self._turns.pop(session_id, None)
        if current is None or current.write_observed:
            return None
        if claims_memory_save(response_text, memory_requested=current.memory_requested):
            return f"{response_text}\n\n{_CORRECTION}"
        return None

    def end_session(self, *, session_id: str = "", **_: object) -> None:
        with self._lock:
            self._turns.pop(session_id, None)
