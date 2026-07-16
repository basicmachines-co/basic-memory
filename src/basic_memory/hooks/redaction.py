"""Stage-1 deterministic redaction floor for captured hook payloads (SPEC-55).

Everything that enters the inbox passes through this floor at capture time.
It combines two layers:

  1. ``detect-secrets`` (Yelp) scanning over every payload string — known token
     formats (AWS ``AKIA…``, GitHub ``ghp_…``, JWTs, private-key blocks, …) plus
     an entropy threshold on long opaque strings.
  2. The recursive deny-key / deny-path / env-pair / truncation rules carried
     over from the #1064 salvage branch, hardened for Windows separators.

Dependency decision (2026-07-15): ``detect-secrets`` is a core dependency, not
an extra. Its tree is light (pyyaml — already core — plus requests), and the
floor must be unconditionally present on the capture hot path: an optional
extra would make redaction availability configuration-dependent, violating the
"Stage 1 · always on" contract. The Stage-2 model scrub (phase 2) is what ships
behind ``basic-memory[redaction]``.

Contract: redaction is pure (never mutates its input) and idempotent
(``redact_payload(redact_payload(p)) == redact_payload(p)``) — the projector
may re-apply it freely.
"""

from __future__ import annotations

import os
import re
from typing import Any

from detect_secrets.core.plugins.util import get_mapping_from_secret_type_to_class
from detect_secrets.core.scan import scan_line
from detect_secrets.plugins.high_entropy_strings import HighEntropyStringsPlugin
from detect_secrets.settings import default_settings

REDACTED = "[REDACTED]"
REDACTED_PATH = "[REDACTED_PATH]"

# Maximum length for any single payload string before truncation.
MAX_PAYLOAD_VALUE_LEN = 500
TRUNCATION_MARKER = "…[truncated]"

# Keys whose values look like secrets, matched case-insensitively against
# payload dict keys as full word segments (delimited by _ or . or string
# boundaries). This catches API_KEY, AUTH_TOKEN, DB_PASSWORD but not
# "safe_key" or "monkey". Users extend the list via extra_redact_keys.
DEFAULT_REDACT_KEY_PATTERNS = (
    re.compile(r"(?i)(?:^|[_.])(?:SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)(?:[_.]|$)"),
    re.compile(r"(?i)(?:^|[_.])(?:API[_.]KEY|ACCESS[_.]KEY|PRIVATE[_.]KEY)(?:[_.]|$)"),
)

# Values that look like environment secrets: KEY=<long-value>.
SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9_]+=.{20,}$")


def _normalize_path(path: str) -> str:
    """Compare paths with forward slashes only.

    ``os.path.expanduser("~/.ssh/")`` yields mixed separators on Windows
    (``C:\\Users\\x/.ssh/``) while native payload values use backslashes, so an
    un-normalized ``startswith`` never matches there.
    """
    return path.replace("\\", "/")


# Sensitive home directories, in the ``~/`` shell form users actually type.
_SENSITIVE_HOME_DIRS = ("~/.ssh/", "~/.aws/", "~/.gnupg/")


def _expand_deny_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize deny-path prefixes into both matchable forms.

    Both forms are denied for each prefix: the expanded absolute path (payload
    values — hook cwd especially — usually carry it resolved) and the literal
    ``~/`` prefix (prose, config, and transcript excerpts commonly write
    ``~/.ssh/id_rsa`` unexpanded — the expanded pattern alone would let that
    survive, and vice versa). dict.fromkeys dedupes while preserving order in
    case expanduser is a no-op (HOME unset, or an already-absolute path).
    """
    expanded = (_normalize_path(os.path.expanduser(prefix)) for prefix in paths)
    literal = (_normalize_path(prefix) for prefix in paths)
    return tuple(dict.fromkeys((*expanded, *literal)))


def _default_redact_paths() -> tuple[str, ...]:
    # Resolved per call, not at import: tests (and long-lived processes) may
    # repoint HOME, and a stale import-time expansion would silently miss.
    return _expand_deny_paths(_SENSITIVE_HOME_DIRS)


def _deny_path_patterns(deny_paths: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Compile each normalized deny-path prefix into a substring matcher.

    Deny paths are stored with forward slashes and a trailing separator. A
    payload value may carry one whole (``~/.ssh/id_rsa``) or embed it mid-string
    in free text (a checkpoint excerpt like ``please read ~/.ssh/id_rsa``), and
    may use native separators, so each ``/`` matches either separator.

    The pattern matches the denied directory **root itself** (``~/.ssh``) as well
    as any descendant (``~/.ssh/id_rsa``): the trailing slash is stripped, then a
    negative-lookahead boundary ``(?![A-Za-z0-9_-])`` rejects only a bare
    alphanumeric/underscore/hyphen continuation — so a sibling like
    ``/srv/clientsbackup`` (for ``/srv/clients/``) can't match — while allowing a
    separator, whitespace, end, or punctuation to end the token. That last part
    matters for prose: a root followed by ``,`` or ``.`` (``read ~/.ssh, then``)
    must still redact. An optional ``[/\\]\\S*`` consumes a descendant when present.

    On Windows the filesystem is case-insensitive, so the payload/transcript may
    carry a different drive/user casing than ``os.path.expanduser`` produced
    (``C:\\Users\\Alice\\.ssh`` vs ``c:\\users\\alice\\.ssh``); the patterns are
    compiled case-insensitively there so the same directory still matches. POSIX
    stays case-sensitive — ``/home/Alice`` and ``/home/alice`` are distinct.
    """
    flags = re.IGNORECASE if os.name == "nt" else 0
    patterns: list[re.Pattern[str]] = []
    for prefix in deny_paths:
        root = prefix.rstrip("/")
        if not root:
            # A bare "/" (or empty) deny path would redact everything; skip it.
            continue
        escaped = re.escape(root).replace("/", r"[/\\]")
        patterns.append(re.compile(escaped + r"(?![A-Za-z0-9_-])(?:[/\\]\S*)?", flags))
    return tuple(patterns)


# --- detect-secrets scanning ---


def _entropy_plugins() -> dict[str, HighEntropyStringsPlugin]:
    """Instantiate the entropy plugins with their default limits, keyed by secret type."""
    return {
        cls.secret_type: cls()
        for cls in get_mapping_from_secret_type_to_class().values()
        if issubclass(cls, HighEntropyStringsPlugin)
    }


def _detected_secret_values(
    line: str, entropy_plugins: dict[str, HighEntropyStringsPlugin]
) -> list[str] | None:
    """Return secret substrings detect-secrets found in ``line``.

    Returns None when a detection cannot be localized to a substring — the
    caller must then redact the whole line.

    Constraint: ``scan_line`` runs entropy plugins in eager mode, which
    deliberately skips their entropy limit so ad-hoc scans can show "why"
    values. That surfaces every token as a candidate, so the limit is re-applied
    here — otherwise ordinary prose would be redacted wholesale.
    """
    values: list[str] = []
    for secret in scan_line(line):
        value = secret.secret_value
        if value is None:  # pragma: no cover - no default plugin emits valueless secrets
            return None
        entropy_plugin = entropy_plugins.get(secret.type)
        if entropy_plugin is not None and (
            entropy_plugin.calculate_shannon_entropy(value) <= entropy_plugin.entropy_limit
        ):
            continue
        values.append(value)
    return values


def _scrub_secrets(value: str, entropy_plugins: dict[str, HighEntropyStringsPlugin]) -> str:
    # detect-secrets plugins are line-oriented; scan each line so a secret in a
    # multi-line payload value is caught just like a single-line one.
    scrubbed_lines: list[str] = []
    for line in value.split("\n"):
        found = _detected_secret_values(line, entropy_plugins)
        if found is None:  # pragma: no cover - see _detected_secret_values
            scrubbed_lines.append(REDACTED)
            continue
        # Longest-first replacement: a detector may report both a full token and
        # a prefix of it; replacing the prefix first would break the full match.
        for secret_value in sorted(set(found), key=len, reverse=True):
            line = line.replace(secret_value, REDACTED)
        scrubbed_lines.append(line)
    return "\n".join(scrubbed_lines)


# --- String-level rules ---


def _truncate(value: str) -> str:
    if len(value) <= MAX_PAYLOAD_VALUE_LEN:
        return value
    # Idempotence: a value truncated by a previous pass is MAX + marker long;
    # truncating it again would chew the marker into the payload text.
    if value.endswith(TRUNCATION_MARKER) and (
        len(value) <= MAX_PAYLOAD_VALUE_LEN + len(TRUNCATION_MARKER)
    ):
        return value
    return value[:MAX_PAYLOAD_VALUE_LEN] + TRUNCATION_MARKER


def _redact_str(
    value: str,
    deny_path_res: tuple[re.Pattern[str], ...],
    entropy_plugins: dict[str, HighEntropyStringsPlugin],
) -> str:
    if SECRET_VALUE_RE.match(value):
        return REDACTED
    # Replace denied-path substrings wherever they occur: a whole-value path
    # collapses to the marker (regex spans the entire string), while a path
    # embedded in prose (checkpoint excerpts, #997) is redacted in place without
    # discarding the surrounding text. Secret/entropy scanning and truncation
    # then run on the remainder.
    for pattern in deny_path_res:
        value = pattern.sub(REDACTED_PATH, value)
    return _truncate(_scrub_secrets(value, entropy_plugins))


# --- Recursive traversal ---


def _redact_value(
    value: Any,
    deny_key_patterns: list[re.Pattern[str]],
    deny_path_res: tuple[re.Pattern[str], ...],
    entropy_plugins: dict[str, HighEntropyStringsPlugin],
) -> Any:
    """Redact a payload value of any JSON-compatible shape.

    Payloads arrive from hook JSON, so nested dicts and lists are normal — a
    secret one level down must be caught just like a top-level one.
    """
    if isinstance(value, str):
        return _redact_str(value, deny_path_res, entropy_plugins)
    if isinstance(value, dict):
        return _redact_dict(value, deny_key_patterns, deny_path_res, entropy_plugins)
    if isinstance(value, (list, tuple)):
        return [
            _redact_value(item, deny_key_patterns, deny_path_res, entropy_plugins) for item in value
        ]
    return value


def _redact_dict(
    payload: dict,
    deny_key_patterns: list[re.Pattern[str]],
    deny_path_res: tuple[re.Pattern[str], ...],
    entropy_plugins: dict[str, HighEntropyStringsPlugin],
) -> dict:
    result: dict = {}
    for key, value in payload.items():
        # A denied key redacts the whole value, however deeply nested —
        # partial redaction inside a secret-named subtree is not worth the risk.
        if any(pattern.search(str(key)) for pattern in deny_key_patterns):
            result[key] = REDACTED
            continue
        result[key] = _redact_value(value, deny_key_patterns, deny_path_res, entropy_plugins)
    return result


def redact_payload(
    payload: dict,
    extra_redact_keys: list[str] | None = None,
    extra_redact_paths: list[str] | None = None,
) -> dict:
    """Strip secrets, denied paths, and oversized values from a payload.

    Returns a new dict with sensitive content replaced by ``[REDACTED]``
    markers, applied recursively over nested dicts and lists. Nothing
    downstream (inbox, projector, artifacts) sees unredacted payload values.
    """
    deny_key_patterns = list(DEFAULT_REDACT_KEY_PATTERNS)
    if extra_redact_keys:
        deny_key_patterns.extend(
            re.compile(re.escape(pattern), re.IGNORECASE) for pattern in extra_redact_keys
        )

    deny_paths = _default_redact_paths()
    if extra_redact_paths:
        # Expand user paths the same way as the built-in defaults: a configured
        # `~/clients/secret` must match the absolute cwd `/home/alice/clients/...`.
        deny_paths = deny_paths + _expand_deny_paths(tuple(extra_redact_paths))
    deny_path_res = _deny_path_patterns(deny_paths)

    # One settings context per payload: detect-secrets reads plugin/filter
    # configuration from process-global settings, and the context both pins the
    # default configuration and restores whatever was active before.
    with default_settings():
        return _redact_dict(payload, deny_key_patterns, deny_path_res, _entropy_plugins())


def redact_text(value: str, extra_redact_paths: list[str] | None = None) -> str:
    """Strip secrets and denied paths from a single free-text string.

    The pre-compaction checkpoint lifts transcript excerpts straight into the
    graph, so that text must pass the same secret floor as inbox payloads
    (issue #997: "redact obvious secrets before writing artifacts"). Key-based
    denial has no meaning for free text; secret/entropy scanning and path denial
    do, so this reuses the per-string floor rather than the dict traversal.
    """
    deny_paths = _default_redact_paths()
    if extra_redact_paths:
        # Expand user paths the same way as the built-in defaults: a configured
        # `~/clients/secret` must match the absolute cwd `/home/alice/clients/...`.
        deny_paths = deny_paths + _expand_deny_paths(tuple(extra_redact_paths))
    with default_settings():
        return _redact_str(value, _deny_path_patterns(deny_paths), _entropy_plugins())
