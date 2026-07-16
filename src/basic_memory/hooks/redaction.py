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

The public surface is the :class:`Redactor` value object: build a ruleset once
(``Redactor.build(...)``) and reuse it across many payloads/strings — redacting
each turn of a transcript must not recompile deny patterns or re-expand paths.
The module-level :func:`redact_payload` / :func:`redact_text` are one-shot
conveniences that build a throwaway redactor for a single value.

Contract: redaction is pure (never mutates its input) and idempotent
(``redact_payload(redact_payload(p)) == redact_payload(p)``) — the projector
may re-apply it freely.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
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

# Sensitive home directories, in the ``~/`` shell form users actually type.
_SENSITIVE_HOME_DIRS = ("~/.ssh/", "~/.aws/", "~/.gnupg/")

# detect-secrets entropy plugins, keyed by the secret type they emit. Rebuilt
# per redaction call inside a ``default_settings()`` context, so this shape is
# passed down the traversal rather than stored on the ruleset.
type EntropyPlugins = dict[str, HighEntropyStringsPlugin]


# --- Path helpers ---


def _normalize_path(path: str) -> str:
    """Compare paths with forward slashes only.

    ``os.path.expanduser("~/.ssh/")`` yields mixed separators on Windows
    (``C:\\Users\\x/.ssh/``) while native payload values use backslashes, so an
    un-normalized ``startswith`` never matches there.
    """
    return path.replace("\\", "/")


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


# --- detect-secrets scanning ---


def _entropy_plugins() -> EntropyPlugins:
    """Instantiate the entropy plugins with their default limits, keyed by secret type."""
    return {
        cls.secret_type: cls()
        for cls in get_mapping_from_secret_type_to_class().values()
        if issubclass(cls, HighEntropyStringsPlugin)
    }


def _detected_secret_values(line: str, entropy_plugins: EntropyPlugins) -> list[str] | None:
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


def _scrub_secrets(value: str, entropy_plugins: EntropyPlugins) -> str:
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


# --- Deny paths ---


@dataclass(frozen=True, slots=True)
class DenyPath:
    """A denied directory as both a normalized ``root`` and its prose matcher.

    Deny paths are stored with forward slashes and a trailing separator. The
    ``root`` (trailing slash stripped) drives the whole-value check, which
    tolerates spaces the substring ``\\S*`` tail would truncate; ``pattern``
    matches a denied path token embedded in free text.
    """

    root: str
    pattern: re.Pattern[str]
    case_insensitive: bool

    @classmethod
    def compile(cls, prefix: str, *, case_insensitive: bool) -> DenyPath | None:
        """Compile a normalized deny-path prefix, or ``None`` to skip it.

        A bare ``"/"`` (or empty) prefix would redact every path, so it is
        skipped. The prose matcher matches the denied directory **root itself**
        (``~/.ssh``) as well as any descendant (``~/.ssh/id_rsa``): a
        negative-lookahead boundary ``(?![A-Za-z0-9_-])`` rejects only a bare
        alphanumeric/underscore/hyphen continuation — so a sibling like
        ``/srv/clientsbackup`` (for ``/srv/clients/``) can't match — while a
        separator, whitespace, end, or punctuation ends the token (prose puts a
        root right before ``,`` or ``.``). The optional ``[/\\]\\S*`` tail
        consumes a descendant up to the next whitespace; a path embedded in
        prose whose directory contains a space is therefore truncated at that
        space (the whole-value check below covers the real capture channel).

        Each ``/`` matches either separator so native Windows backslash values
        match a forward-slash deny path. On Windows the filesystem is
        case-insensitive, so the pattern is compiled case-insensitively there
        (``C:\\Users\\Alice\\.ssh`` == ``c:\\users\\alice\\.ssh``); POSIX stays
        case-sensitive (``/home/Alice`` and ``/home/alice`` are distinct).
        """
        root = prefix.rstrip("/")
        if not root:
            return None
        escaped = re.escape(root).replace("/", r"[/\\]")
        flags = re.IGNORECASE if case_insensitive else 0
        pattern = re.compile(escaped + r"(?![A-Za-z0-9_-])(?:[/\\]\S*)?", flags)
        return cls(root=root, pattern=pattern, case_insensitive=case_insensitive)

    def matches_whole(self, normalized_value: str) -> bool:
        """Whether ``normalized_value`` is, in full, this directory or a descendant.

        Path-prefix logic on the whole value, so a spaced path (a cwd like
        ``/srv/clients/acme corp/repo``) is caught — the ``pattern`` tail would
        stop at the first space and leak the rest. Case-folded when the ruleset
        is case-insensitive to match the Windows filesystem.
        """
        candidate = normalized_value.casefold() if self.case_insensitive else normalized_value
        target = self.root.casefold() if self.case_insensitive else self.root
        return candidate == target or candidate.startswith(target + "/")


# --- The redactor ---


@dataclass(frozen=True, slots=True)
class Redactor:
    """A compiled Stage-1 redaction ruleset, reusable across many values.

    Build once (:meth:`build`) and reuse: the deny-key and deny-path patterns
    are compiled up front so redacting each turn of a transcript does not
    recompile the ruleset or re-expand paths. Redaction is pure (never mutates
    its input) and idempotent.
    """

    deny_key_patterns: tuple[re.Pattern[str], ...]
    deny_paths: tuple[DenyPath, ...]

    @classmethod
    def build(
        cls,
        *,
        extra_redact_keys: list[str] | None = None,
        extra_redact_paths: list[str] | None = None,
    ) -> Redactor:
        """Compile the default ruleset, extended with caller-supplied deny rules.

        ``extra_redact_paths`` are expanded like the built-in defaults: a
        configured ``~/clients/secret`` must match the absolute cwd
        ``/home/alice/clients/...`` a hook actually captures.
        """
        key_patterns = list(DEFAULT_REDACT_KEY_PATTERNS)
        if extra_redact_keys:
            key_patterns.extend(
                re.compile(re.escape(pattern), re.IGNORECASE) for pattern in extra_redact_keys
            )

        prefixes = _default_redact_paths()
        if extra_redact_paths:
            prefixes = prefixes + _expand_deny_paths(tuple(extra_redact_paths))

        # Read os.name live (not at import): tests repoint it and the same
        # interpreter serves one platform for its whole life, so build-time is
        # the right, cheap place to settle case sensitivity for the ruleset.
        case_insensitive = os.name == "nt"
        deny_paths = tuple(
            path
            for prefix in prefixes
            if (path := DenyPath.compile(prefix, case_insensitive=case_insensitive)) is not None
        )
        return cls(deny_key_patterns=tuple(key_patterns), deny_paths=deny_paths)

    def redact_payload(self, payload: dict) -> dict:
        """Return a copy of ``payload`` with secrets, denied paths, and oversized
        values replaced by markers, recursively over nested dicts and lists.

        Nothing downstream (inbox, projector, artifacts) sees unredacted values.
        """
        # One settings context per payload: detect-secrets reads plugin/filter
        # configuration from process-global settings, and the context both pins
        # the default configuration and restores whatever was active before.
        with default_settings():
            return self._redact_dict(payload, _entropy_plugins())

    def redact_text(self, value: str) -> str:
        """Return ``value`` with secrets and denied paths replaced by markers.

        Key-based denial has no meaning for free text; this runs the per-string
        floor (secret/entropy scanning + path denial) that payload strings get.
        """
        with default_settings():
            return self._redact_str(value, _entropy_plugins())

    # --- traversal ---

    def _redact_str(self, value: str, entropy_plugins: EntropyPlugins) -> str:
        if SECRET_VALUE_RE.match(value):
            return REDACTED
        # A value that is wholly a denied path (or a descendant) collapses to the
        # marker via path-prefix logic, so spaces in the path don't leak.
        normalized = _normalize_path(value)
        if any(path.matches_whole(normalized) for path in self.deny_paths):
            return REDACTED_PATH
        # Otherwise replace any denied-path token embedded in prose (checkpoint
        # excerpts, #997) in place, then run secret/entropy scanning + truncation
        # on the remainder.
        for path in self.deny_paths:
            value = path.pattern.sub(REDACTED_PATH, value)
        return _truncate(_scrub_secrets(value, entropy_plugins))

    def _redact_value(self, value: Any, entropy_plugins: EntropyPlugins) -> Any:
        """Redact a payload value of any JSON-compatible shape.

        Payloads arrive from hook JSON, so nested dicts and lists are normal — a
        secret one level down must be caught just like a top-level one.
        """
        if isinstance(value, str):
            return self._redact_str(value, entropy_plugins)
        if isinstance(value, dict):
            return self._redact_dict(value, entropy_plugins)
        if isinstance(value, (list, tuple)):
            return [self._redact_value(item, entropy_plugins) for item in value]
        return value

    def _redact_dict(self, payload: dict, entropy_plugins: EntropyPlugins) -> dict:
        result: dict = {}
        for key, value in payload.items():
            # A denied key redacts the whole value, however deeply nested —
            # partial redaction inside a secret-named subtree is not worth the risk.
            if any(pattern.search(str(key)) for pattern in self.deny_key_patterns):
                result[key] = REDACTED
                continue
            result[key] = self._redact_value(value, entropy_plugins)
        return result


# --- One-shot convenience wrappers ---


def redact_payload(
    payload: dict,
    extra_redact_keys: list[str] | None = None,
    extra_redact_paths: list[str] | None = None,
) -> dict:
    """Redact a single payload with a throwaway ruleset.

    Reuse a :class:`Redactor` instead when redacting many values (e.g. every
    turn of a transcript) so the ruleset is compiled once.
    """
    redactor = Redactor.build(
        extra_redact_keys=extra_redact_keys, extra_redact_paths=extra_redact_paths
    )
    return redactor.redact_payload(payload)


def redact_text(value: str, extra_redact_paths: list[str] | None = None) -> str:
    """Redact a single free-text string with a throwaway ruleset.

    The pre-compaction checkpoint lifts transcript excerpts straight into the
    graph, so that text must pass the same secret floor as inbox payloads
    (issue #997). Reuse a :class:`Redactor` when scrubbing many strings.
    """
    return Redactor.build(extra_redact_paths=extra_redact_paths).redact_text(value)
