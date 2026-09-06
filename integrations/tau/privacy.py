"""Conservative masking of common credential forms in automatic memory.

This is not a general secret detector. Hidden reasoning and tool payloads are
excluded structurally before this filter; users should disable capture for
sensitive conversations whose public text cannot leave the session.
"""

import re

PATTERNS = (
    re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\b(?:sk-|bmc_|ghp_|github_pat_|hf_)[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?i)\bBearer\s+[^\s\"']+"),
    re.compile(r"(?i)\b(?:[A-Z0-9_]*(?:API_KEY|PASSWORD|SECRET|ACCESS_TOKEN))\s*[:=]\s*[^\s,;]+"),
)


def public_text(text: str) -> str:
    for pattern in PATTERNS:
        text = pattern.sub("[credential redacted]", text)
    return text
