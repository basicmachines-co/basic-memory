"""Unit tests for the Stage-1 deterministic redaction floor."""

import copy
from pathlib import Path

from basic_memory.hooks.redaction import (
    MAX_PAYLOAD_VALUE_LEN,
    REDACTED,
    REDACTED_PATH,
    TRUNCATION_MARKER,
    redact_payload,
    redact_text,
)

# --- Deny-key rules (recursive) ---


def test_redacts_nested_dict_secrets_by_key() -> None:
    payload = {"config": {"api_key": "sk-" + "a" * 30, "region": "us-east-1"}}

    redacted = redact_payload(payload)

    assert redacted["config"]["api_key"] == REDACTED
    assert redacted["config"]["region"] == "us-east-1"


def test_redacts_secrets_inside_lists() -> None:
    payload = {
        "env_dump": ["PATH=/usr/bin", "AWS_SECRET_ACCESS_KEY=" + "s" * 30],
        "steps": [{"auth_token": "t" * 30}, {"note": "safe"}],
    }

    redacted = redact_payload(payload)

    assert redacted["env_dump"][0] == "PATH=/usr/bin"
    assert redacted["env_dump"][1] == REDACTED
    assert redacted["steps"][0]["auth_token"] == REDACTED
    assert redacted["steps"][1]["note"] == "safe"


def test_denied_key_redacts_whole_subtree() -> None:
    payload = {"auth": {"user": "alice", "nested": {"deep": "value"}}}

    assert redact_payload(payload)["auth"] == REDACTED


def test_benign_key_names_pass_through() -> None:
    payload = {"safe_key": "value", "monkey": "value"}

    assert redact_payload(payload) == payload


def test_extra_keys_apply_at_depth() -> None:
    payload = {"outer": {"internal_id": "abc"}}

    redacted = redact_payload(payload, extra_redact_keys=["internal_id"])

    assert redacted["outer"]["internal_id"] == REDACTED


def test_non_string_scalars_pass_through() -> None:
    payload = {"count": 3, "ratio": 0.5, "flag": True, "nothing": None}

    assert redact_payload(payload) == payload


def test_tuples_normalize_to_lists() -> None:
    assert redact_payload({"steps": ("a", "b")})["steps"] == ["a", "b"]


# --- Deny-path rules ---


def test_deny_paths_apply_at_depth() -> None:
    home_ssh = str(Path("~/.ssh/id_rsa").expanduser())
    payload = {"files": [{"path": home_ssh, "preview": "y" * 600}]}

    redacted = redact_payload(payload)

    entry = redacted["files"][0]
    assert entry["path"] == REDACTED_PATH
    assert entry["preview"].endswith(TRUNCATION_MARKER)
    assert len(entry["preview"]) < 600


def test_deny_paths_redact_embedded_substring_in_value() -> None:
    # A payload string value can embed a secret path mid-text; only the path
    # token is replaced, the rest of the value is preserved.
    home_ssh = str(Path("~/.ssh/id_rsa").expanduser())
    redacted = redact_payload({"note": f"copied {home_ssh} to backup"})

    assert home_ssh not in redacted["note"]
    assert redacted["note"] == f"copied {REDACTED_PATH} to backup"


def test_deny_paths_match_across_windows_separators() -> None:
    # Windows payload values carry backslashes while deny paths are usually
    # written with forward slashes; both sides normalize before comparison.
    payload = {"path": "C:\\Users\\dev\\vault\\key.txt"}

    redacted = redact_payload(payload, extra_redact_paths=["C:/Users/dev/vault/"])

    assert redacted["path"] == REDACTED_PATH


def test_extra_deny_paths_accept_backslash_prefixes() -> None:
    payload = {"path": "C:/Users/dev/vault/key.txt"}

    redacted = redact_payload(payload, extra_redact_paths=["C:\\Users\\dev\\vault\\"])

    assert redacted["path"] == REDACTED_PATH


# --- Env-pair and truncation rules ---


def test_env_style_pairs_redact_wholesale() -> None:
    assert redact_payload({"line": "MY_TOKEN_VALUE=" + "v" * 25})["line"] == REDACTED


def test_truncation_caps_long_values() -> None:
    redacted = redact_payload({"long": "z" * (MAX_PAYLOAD_VALUE_LEN + 100)})

    assert redacted["long"] == "z" * MAX_PAYLOAD_VALUE_LEN + TRUNCATION_MARKER


# --- detect-secrets hits ---


def test_detect_secrets_redacts_aws_key_in_prose() -> None:
    payload = {"opening": "use key AKIAIOSFODNN7EXAMPLE for the deploy"}

    redacted = redact_payload(payload)

    assert "AKIAIOSFODNN7EXAMPLE" not in redacted["opening"]
    assert REDACTED in redacted["opening"]
    assert "for the deploy" in redacted["opening"]


def test_detect_secrets_redacts_github_token() -> None:
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    redacted = redact_payload({"opening": f"push with {token} now"})

    assert token not in redacted["opening"]


def test_detect_secrets_redacts_keyword_assignments() -> None:
    redacted = redact_payload({"opening": "password = 'hunter2-super-secret-value'"})

    assert "hunter2-super-secret-value" not in redacted["opening"]


def test_detect_secrets_redacts_private_key_blocks_multiline() -> None:
    value = "context\n-----BEGIN RSA PRIVATE KEY-----\nplain trailing line"
    redacted = redact_payload({"dump": value})

    assert "BEGIN RSA PRIVATE KEY" not in redacted["dump"]
    assert "plain trailing line" in redacted["dump"]


def test_detect_secrets_redacts_high_entropy_strings() -> None:
    opaque = "Zm9vYmFyYmF6cXV4cXV1eDEyMzQ1Njc4OTBhYmNkZWY="
    redacted = redact_payload({"opening": f"blob {opaque} end"})

    assert opaque not in redacted["opening"]
    assert "end" in redacted["opening"]


def test_ordinary_prose_survives_entropy_scan() -> None:
    prose = "totally benign sentence about coffee brewing methods"

    assert redact_payload({"opening": prose})["opening"] == prose


# --- Purity and idempotence ---


def test_redaction_is_pure() -> None:
    payload = {"config": {"api_key": "sk-" + "a" * 30}, "items": ["MY_TOKEN=" + "v" * 25]}
    snapshot = copy.deepcopy(payload)

    redact_payload(payload)

    assert payload == snapshot


def test_redaction_is_idempotent() -> None:
    payload = {
        "config": {"api_key": "sk-" + "a" * 30},
        "opening": "use key AKIAIOSFODNN7EXAMPLE plus password = 'hunter2-super-secret-value'",
        "long": "z" * (MAX_PAYLOAD_VALUE_LEN + 100),
        "env": "MY_TOKEN_VALUE=" + "v" * 25,
        "nested": [{"path": "C:\\Users\\dev\\vault\\key.txt"}],
    }

    once = redact_payload(payload, extra_redact_paths=["C:/Users/dev/vault/"])
    twice = redact_payload(once, extra_redact_paths=["C:/Users/dev/vault/"])

    assert once == twice


# --- redact_text: single free-text string (checkpoint excerpts, #997) ---


def test_redact_text_scrubs_secret_embedded_in_prose() -> None:
    scrubbed = redact_text("deploy with AKIAIOSFODNN7EXAMPLE now")

    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed
    assert "deploy with" in scrubbed


def test_redact_text_leaves_ordinary_prose_intact() -> None:
    assert redact_text("Fix the login bug in the auth handler") == (
        "Fix the login bug in the auth handler"
    )


def test_redact_text_redacts_denied_path() -> None:
    home_ssh = str(Path("~/.ssh/id_rsa").expanduser())
    assert redact_text(home_ssh) == REDACTED_PATH


def test_redact_text_honors_extra_deny_paths() -> None:
    assert redact_text("/srv/secrets/prod.env", extra_redact_paths=["/srv/secrets/"]) == (
        REDACTED_PATH
    )


def test_redact_text_redacts_exact_denied_directory_root() -> None:
    # The denied directory itself (no trailing separator, no child) must redact,
    # not only its descendants.
    assert redact_text("/srv/clients", extra_redact_paths=["/srv/clients/"]) == REDACTED_PATH
    home_ssh = str(Path("~/.ssh").expanduser())
    assert redact_text(home_ssh) == REDACTED_PATH


def test_redact_text_leaves_sibling_of_denied_directory_intact() -> None:
    # A sibling sharing the prefix chars must not match (bounded root).
    assert redact_text("/srv/clientsbackup", extra_redact_paths=["/srv/clients/"]) == (
        "/srv/clientsbackup"
    )


def test_redact_text_ignores_bare_root_deny_path() -> None:
    # A "/" (or empty) deny path would otherwise redact every path; it's skipped.
    assert redact_text("/opt/app/main.py", extra_redact_paths=["/"]) == "/opt/app/main.py"


def test_redact_text_expands_user_tilde_deny_path() -> None:
    # A user-configured redactPaths entry in ~/ form must match the absolute cwd
    # the hook actually captures (expanded like the built-in defaults).
    absolute = str(Path("~/clients/secret/repo").expanduser())
    scrubbed = redact_text(f"working in {absolute}", extra_redact_paths=["~/clients/secret"])

    assert absolute not in scrubbed
    assert scrubbed == f"working in {REDACTED_PATH}"


def test_redact_text_redacts_denied_root_before_punctuation() -> None:
    # Prose puts punctuation right after a path; the root must still redact.
    home_ssh = str(Path("~/.ssh").expanduser())
    assert redact_text(f"key at {home_ssh}, done") == f"key at {REDACTED_PATH}, done"
    assert redact_text("/srv/clients.", extra_redact_paths=["/srv/clients/"]) == f"{REDACTED_PATH}."


def test_redact_text_redacts_denied_path_embedded_in_prose() -> None:
    # A checkpoint excerpt may reference a secret path mid-sentence; the whole
    # path token is replaced in place while the surrounding prose survives.
    home_ssh = str(Path("~/.ssh/id_rsa").expanduser())
    scrubbed = redact_text(f"please read {home_ssh} then continue")

    assert home_ssh not in scrubbed
    assert scrubbed == f"please read {REDACTED_PATH} then continue"


def test_redact_text_redacts_multiple_embedded_paths() -> None:
    ssh = str(Path("~/.ssh/id_rsa").expanduser())
    aws = str(Path("~/.aws/credentials").expanduser())
    scrubbed = redact_text(f"compare {ssh} and {aws} carefully")

    assert ssh not in scrubbed
    assert aws not in scrubbed
    assert scrubbed == f"compare {REDACTED_PATH} and {REDACTED_PATH} carefully"


def test_redact_text_redacts_unexpanded_tilde_home_path() -> None:
    # Prose commonly names the shell form (~/.ssh/id_rsa) rather than the
    # expanded absolute path; the literal ~/ prefix must be denied too.
    scrubbed = redact_text("please read ~/.ssh/id_rsa then continue")

    assert "~/.ssh/id_rsa" not in scrubbed
    assert scrubbed == f"please read {REDACTED_PATH} then continue"


def test_redact_payload_redacts_unexpanded_tilde_home_path() -> None:
    redacted = redact_payload({"note": "key at ~/.aws/credentials please"})

    assert "~/.aws/credentials" not in redacted["note"]
    assert redacted["note"] == f"key at {REDACTED_PATH} please"
