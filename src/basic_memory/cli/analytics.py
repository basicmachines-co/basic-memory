"""Lightweight CLI analytics via Umami event collector.

Sends anonymous, non-blocking usage events to help understand how the
CLI-to-cloud conversion funnel performs. No PII, no fingerprinting,
no cookies. Respects the same opt-out mechanisms as promo messaging.

Events are fire-and-forget — analytics never blocks or breaks the CLI.

Setup:
    Set these environment variables (or leave unset to disable):
        BASIC_MEMORY_UMAMI_HOST     — Umami instance URL (e.g. https://analytics.basicmemory.com)
        BASIC_MEMORY_UMAMI_SITE_ID  — Website ID from Umami dashboard
"""

import json
import os
import threading
import urllib.request
from typing import Optional

import basic_memory


# ---------------------------------------------------------------------------
# Configuration — read from environment so nothing is hard-coded in source
# ---------------------------------------------------------------------------

def _umami_host() -> Optional[str]:
    return os.getenv("BASIC_MEMORY_UMAMI_HOST", "").strip() or None


def _umami_site_id() -> Optional[str]:
    return os.getenv("BASIC_MEMORY_UMAMI_SITE_ID", "").strip() or None


def _analytics_disabled() -> bool:
    """True when analytics should not fire."""
    value = os.getenv("BASIC_MEMORY_NO_PROMOS", "").strip().lower()
    return value in {"1", "true", "yes"}


def _is_configured() -> bool:
    """True when both host and site ID are available."""
    return _umami_host() is not None and _umami_site_id() is not None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Well-known event names for the promo/cloud funnel
EVENT_PROMO_SHOWN = "cli-promo-shown"
EVENT_PROMO_OPTED_OUT = "cli-promo-opted-out"
EVENT_CLOUD_LOGIN_STARTED = "cli-cloud-login-started"
EVENT_CLOUD_LOGIN_SUCCESS = "cli-cloud-login-success"
EVENT_CLOUD_LOGIN_SUB_REQUIRED = "cli-cloud-login-sub-required"


def track(event_name: str, data: Optional[dict] = None) -> None:
    """Send an analytics event to Umami. Non-blocking, silent on failure.

    Parameters
    ----------
    event_name:
        Short kebab-case name (e.g. "cli-promo-shown").
    data:
        Optional dict of event properties (all values should be strings/numbers).
    """
    if _analytics_disabled() or not _is_configured():
        return

    host = _umami_host()
    site_id = _umami_site_id()

    payload = {
        "payload": {
            "hostname": "cli.basicmemory.com",
            "language": "en",
            "url": f"/cli/{event_name}",
            "website": site_id,
            "name": event_name,
            "data": {
                "version": basic_memory.__version__,
                **(data or {}),
            },
        }
    }

    def _send():
        try:
            req = urllib.request.Request(
                f"{host}/api/send",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"basic-memory-cli/{basic_memory.__version__}",
                },
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass  # Never break the CLI for analytics

    threading.Thread(target=_send, daemon=True).start()
