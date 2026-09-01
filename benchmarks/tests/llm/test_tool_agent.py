"""Tests for the tool-use model seam (spec parsing and both transports)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import basic_memory_benchmarks.llm.tool_agent as tool_agent
from basic_memory_benchmarks.llm.runners import LLMRunnerError
from basic_memory_benchmarks.llm.tool_agent import (
    AssistantTurn,
    OpenAICompatToolAgent,
    ScriptedToolAgent,
    ToolCall,
    ToolDef,
    ToolReturn,
    UserMessage,
    create_tool_agent_model,
    substitute_placeholders,
)

SEARCH_TOOL = ToolDef(
    name="search_notes",
    description="Search notes",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
)


class TestCreateToolAgentModel:
    def test_openai_compat_spec(self) -> None:
        model = create_tool_agent_model("openai-compat:qwen3@http://localhost:11434/v1")
        assert isinstance(model, OpenAICompatToolAgent)
        assert model.model == "qwen3"
        assert model.base_url == "http://localhost:11434/v1"

    def test_scripted_spec(self, tmp_path: Path) -> None:
        script_path = tmp_path / "script.json"
        script_path.write_text(json.dumps({"tasks": {"hello": [{"text": "hi"}]}}))
        model = create_tool_agent_model(f"scripted:{script_path}")
        assert isinstance(model, ScriptedToolAgent)
        assert model.spec == f"scripted:{script_path}"

    def test_claude_rejected_with_explanation(self) -> None:
        with pytest.raises(ValueError, match="single-shot.*tool_use|tool_use.*single-shot"):
            create_tool_agent_model("claude:claude-haiku-4-5")

    def test_junk_rejected(self) -> None:
        with pytest.raises(ValueError):
            create_tool_agent_model("gemini:flash")
        with pytest.raises(ValueError):
            create_tool_agent_model("openai-compat:no-base-url")

    def test_scripted_file_without_tasks_rejected(self, tmp_path: Path) -> None:
        script_path = tmp_path / "bad.json"
        script_path.write_text(json.dumps({"not_tasks": {}}))
        with pytest.raises(ValueError, match="tasks"):
            create_tool_agent_model(f"scripted:{script_path}")


def _response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json=payload,
        request=httpx.Request("POST", "http://localhost/v1/chat/completions"),
    )


def test_extra_headers_ride_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """--model-header values reach the endpoint (e.g. anthropic-workspace-id)."""
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["headers"] = kwargs["headers"]
        return _response(
            {
                "choices": [{"message": {"content": "ok", "tool_calls": []}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
    agent = create_tool_agent_model(
        "openai-compat:claude-sonnet-5@https://api.anthropic.com/v1",
        api_key="k",
        extra_headers={"anthropic-workspace-id": "wrkspc_test"},
    )
    assert isinstance(agent, OpenAICompatToolAgent)
    agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    assert captured["headers"]["anthropic-workspace-id"] == "wrkspc_test"
    assert captured["headers"]["Authorization"] == "Bearer k"


AMBIENT_KEY = "sk-ambient-must-not-be-sent"
OPERATOR_AUTHORIZATION = "Bearer op-token-intended"


def _capturing_post(captured: dict[str, Any]) -> Any:
    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["headers"] = kwargs["headers"]
        return _response(
            {
                "choices": [{"message": {"content": "ok", "tool_calls": []}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    return fake_post


def _authorization_values(headers: Any) -> list[bytes]:
    """Every Authorization value as httpx would put it on the wire.

    Reads the raw list rather than indexing, because indexing collapses the
    duplicate this asserts the absence of. Wrapping in ``httpx.Headers`` is
    what a plain dict would go through inside httpx anyway, so a dict spelling
    the name twice still yields two entries here.
    """
    return [value for name, value in httpx.Headers(headers).raw if name.lower() == b"authorization"]


def test_operator_authorization_header_replaces_the_ambient_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A differently-cased operator header must replace the bearer, not join it.

    HTTP header names are case-insensitive, so sending both is not "two
    headers": the endpoint picks one, and the ambient OPENAI_API_KEY — which
    the operator exported for some other tool — is disclosed to a custom
    endpoint they never meant to hand it to.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(tool_agent.httpx, "post", _capturing_post(captured))
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)

    agent = create_tool_agent_model(
        "openai-compat:m@http://localhost/v1",
        extra_headers={"authorization": OPERATOR_AUTHORIZATION},
    )
    agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    assert _authorization_values(captured["headers"]) == [OPERATOR_AUTHORIZATION.encode()]
    # The ambient credential must not ride along under any header name.
    assert all(
        AMBIENT_KEY.encode() not in value for _, value in httpx.Headers(captured["headers"]).raw
    )


def test_repeated_model_header_spellings_collapse_to_the_last_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--model-header is repeatable and its names are case-sensitive at parse.

    So the operator's own header map can spell one HTTP field two ways, which
    is the same duplicate-serialization defect without an ambient key involved.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(tool_agent.httpx, "post", _capturing_post(captured))

    agent = OpenAICompatToolAgent(
        "m",
        "http://localhost/v1",
        extra_headers={"Authorization": "Bearer first", "authorization": OPERATOR_AUTHORIZATION},
    )
    agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    assert _authorization_values(captured["headers"]) == [OPERATOR_AUTHORIZATION.encode()]


def test_temperature_none_omits_the_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude 5 endpoints reject any temperature; None must drop the key entirely."""
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["body"] = kwargs["json"]
        return _response(
            {
                "choices": [{"message": {"content": "ok", "tool_calls": []}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
    agent = OpenAICompatToolAgent("m", "http://localhost/v1", temperature=None)
    agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    assert "temperature" not in captured["body"]


def test_http_error_includes_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4xx body names the actual rejection instead of a bare status code."""

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={"error": {"message": "anthropic-workspace-id is required"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
    agent = OpenAICompatToolAgent("m", "http://localhost/v1", max_retries=0)
    with pytest.raises(LLMRunnerError, match="anthropic-workspace-id is required"):
        agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])


SECRET_KEY = "sk-live-0123456789abcdef"
SECRET_HEADER_VALUE = "wrkspc_sensitive_9999"


def _echoing_401(url: str, **kwargs: Any) -> httpx.Response:
    """A gateway that quotes the offending request headers back in its body."""
    return httpx.Response(
        status_code=401,
        json={
            "error": {
                "message": "invalid api key",
                "request_headers": {
                    "Authorization": f"Bearer {SECRET_KEY}",
                    "anthropic-workspace-id": SECRET_HEADER_VALUE,
                },
            }
        },
        request=httpx.Request("POST", url),
    )


def _secretive_agent(**kwargs: Any) -> OpenAICompatToolAgent:
    return OpenAICompatToolAgent(
        "m",
        "http://localhost/v1",
        api_key=SECRET_KEY,
        extra_headers={"anthropic-workspace-id": SECRET_HEADER_VALUE},
        max_retries=0,
        **kwargs,
    )


def test_error_body_redacts_secrets_but_keeps_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An echoed key/header must not ride the error into run artifacts."""
    monkeypatch.setattr(tool_agent.httpx, "post", _echoing_401)

    with pytest.raises(LLMRunnerError) as caught:
        _secretive_agent().propose([UserMessage(text="hi")], [SEARCH_TOOL])

    message = str(caught.value)
    assert SECRET_KEY not in message
    assert SECRET_HEADER_VALUE not in message
    assert tool_agent.REDACTION_MARKER in message
    # The diagnostic the body was included for must survive redaction.
    assert "invalid api key" in message


def test_redaction_precedes_body_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret straddling the 300-char cut must not survive as a prefix."""

    # 290 padding + a 20-char key: a naive text[:300] keeps the first 10
    # characters of the key, which is what this test must catch.
    def padded_401(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            status_code=401,
            text=("x" * 290) + SECRET_KEY,
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tool_agent.httpx, "post", padded_401)

    with pytest.raises(LLMRunnerError) as caught:
        _secretive_agent().propose([UserMessage(text="hi")], [SEARCH_TOOL])

    assert SECRET_KEY[:10] not in str(caught.value)


def test_redact_secrets_masks_longest_match_first() -> None:
    # A short secret contained in a longer one must not be masked first, or
    # the remainder of the longer value would stay in the text.
    masked = tool_agent.redact_secrets("token=abc123-suffix", ["abc123", "abc123-suffix"])
    assert masked == f"token={tool_agent.REDACTION_MARKER}"


def test_redact_secrets_without_secrets_is_identity() -> None:
    assert tool_agent.redact_secrets("nothing to hide", []) == "nothing to hide"


# HTTP allows any visible ASCII character in a header value, so " (0x22) and
# \ (0x5C) are both legal in a --model-header secret — and both are escaped
# when a gateway echoes the value inside a JSON error body.
ESCAPING_SECRET = 'wrk"space\\9999'


def test_redact_secrets_masks_the_json_escaped_spelling() -> None:
    """A secret echoed into a JSON body appears escaped, not plaintext."""
    body = json.dumps({"error": {"message": "invalid api key", "seen": ESCAPING_SECRET}})
    escaped = json.dumps(ESCAPING_SECRET)[1:-1]
    # Precondition: the plaintext genuinely is absent, so a plaintext-only
    # search has nothing to match and would leave the body untouched.
    assert ESCAPING_SECRET not in body
    assert escaped in body

    masked = tool_agent.redact_secrets(body, [ESCAPING_SECRET])

    assert escaped not in masked
    assert tool_agent.REDACTION_MARKER in masked
    assert "invalid api key" in masked


def test_redact_secrets_masks_a_secret_nested_two_json_levels_deep() -> None:
    """A proxy that wraps an upstream JSON body in a string escapes it twice."""
    upstream = json.dumps({"error": {"seen": ESCAPING_SECRET}})
    body = json.dumps({"error": {"message": "upstream rejected", "upstream": upstream}})
    double_escaped = json.dumps(json.dumps(ESCAPING_SECRET)[1:-1])[1:-1]
    assert double_escaped in body

    masked = tool_agent.redact_secrets(body, [ESCAPING_SECRET])

    assert double_escaped not in masked
    assert "upstream rejected" in masked


def test_error_body_redacts_a_json_escaping_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end at the seam: the escaped spelling must not reach the error."""

    def echoing_401(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            status_code=401,
            json={"error": {"message": "invalid api key", "seen": ESCAPING_SECRET}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tool_agent.httpx, "post", echoing_401)
    agent = OpenAICompatToolAgent(
        "m",
        "http://localhost/v1",
        extra_headers={"anthropic-workspace-id": ESCAPING_SECRET},
        max_retries=0,
    )

    with pytest.raises(LLMRunnerError) as caught:
        agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    message = str(caught.value)
    assert ESCAPING_SECRET not in message
    assert json.dumps(ESCAPING_SECRET)[1:-1] not in message
    assert "invalid api key" in message


def _unicode_escape(character: str, *, uppercase: bool = False) -> str:
    """The JSON ``\\uXXXX`` spelling of one character.

    Built rather than written literally so the tests state which character is
    being escaped, and so the same helper produces both hex cases.
    """
    return f"\\u{ord(character):04X}" if uppercase else f"\\u{ord(character):04x}"


def _go_encoded(value: str) -> str:
    """``value`` as Go's default JSON encoder spells it inside a string.

    Go HTML-escapes ``<``, ``>`` and ``&`` unless the caller opts out, so a
    gateway written in Go echoes a header value in a spelling ``json.dumps``
    never produces and ``_encoded_forms`` therefore cannot enumerate.
    """
    return "".join(
        _unicode_escape(character) if character in "<>&" else character for character in value
    )


# A --model-header value is any visible ASCII, so it may contain the characters
# a Go encoder escapes. This is the value from the reported reproduction.
GO_ESCAPING_SECRET = "wrk<secret>"


def test_redact_secrets_withholds_a_body_using_go_style_unicode_escapes() -> None:
    """The reported leak: a valid spelling that masking alone cannot reach."""
    body = '{"seen":"' + _go_encoded(GO_ESCAPING_SECRET) + '"}'
    # Precondition: neither the plaintext nor any json.dumps spelling is
    # present, so every form _encoded_forms derives provably fails to match
    # and the body would otherwise pass through untouched.
    assert GO_ESCAPING_SECRET not in body
    assert json.dumps(GO_ESCAPING_SECRET)[1:-1] not in body

    masked = tool_agent.redact_secrets(body, [GO_ESCAPING_SECRET])

    assert masked == tool_agent.WITHHELD_BODY_MARKER
    assert _go_encoded(GO_ESCAPING_SECRET) not in masked


def test_redact_secrets_detects_unicode_escapes_in_either_hex_case() -> None:
    """JSON allows both hex cases, so neither spelling may be privileged."""
    lower = '{"seen":"wrk' + _unicode_escape("<") + "secret" + _unicode_escape(">") + '"}'
    upper = (
        '{"seen":"wrk'
        + _unicode_escape("<", uppercase=True)
        + "secret"
        + _unicode_escape(">", uppercase=True)
        + '"}'
    )
    assert lower != upper

    for body in (lower, upper):
        assert tool_agent.redact_secrets(body, [GO_ESCAPING_SECRET]) == (
            tool_agent.WITHHELD_BODY_MARKER
        )


def test_redact_secrets_handles_a_truncated_body_without_parsing_it() -> None:
    """Error bodies arrive cut mid-string; a json.loads backstop would raise."""
    body = '{"error":{"seen":"wrk' + _unicode_escape("<") + "secret" + _unicode_escape(">")
    with pytest.raises(json.JSONDecodeError):
        json.loads(body)

    assert tool_agent.redact_secrets(body, [GO_ESCAPING_SECRET]) == (
        tool_agent.WITHHELD_BODY_MARKER
    )


def test_redact_secrets_detects_a_doubly_escaped_unicode_spelling() -> None:
    """A proxy wrapping an upstream body escapes the upstream's own escapes."""
    upstream = '{"seen":"' + _go_encoded(GO_ESCAPING_SECRET) + '"}'
    body = json.dumps({"error": {"message": "upstream rejected", "upstream": upstream}})
    # One pass only recovers the upstream text; the secret needs the second,
    # which is what the fixpoint loop (rather than a single unescape) buys.
    assert GO_ESCAPING_SECRET not in tool_agent._unescape_once(body)

    assert tool_agent.redact_secrets(body, [GO_ESCAPING_SECRET]) == (
        tool_agent.WITHHELD_BODY_MARKER
    )


def test_redact_secrets_keeps_an_ordinary_body_readable() -> None:
    """Withholding is the exception: a maskable body keeps its diagnostic."""
    body = json.dumps({"error": {"message": "invalid api key", "key": SECRET_KEY}})

    masked = tool_agent.redact_secrets(body, [SECRET_KEY])

    assert SECRET_KEY not in masked
    assert tool_agent.REDACTION_MARKER in masked
    assert "invalid api key" in masked
    assert masked != tool_agent.WITHHELD_BODY_MARKER


# A control escape must decode to its character rather than merely lose its
# backslash: dropping it would splice "…i" and "formation" into a literal match
# and withhold a multi-line diagnostic that never contained the secret at all.
NEWLINE_STRADDLING_SECRET = "wrkspc_information"


def test_redact_secrets_decodes_control_escapes_rather_than_dropping_them() -> None:
    body = json.dumps({"error": "check wrkspc_i\nformation and retry"})
    assert NEWLINE_STRADDLING_SECRET not in body

    assert tool_agent.redact_secrets(body, [NEWLINE_STRADDLING_SECRET]) == body


def test_error_body_withheld_when_a_secret_survives_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a Go-style echo costs the body, never the credential."""

    def go_escaping_401(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            status_code=401,
            text='{"error":{"message":"invalid api key","seen":"'
            + _go_encoded(GO_ESCAPING_SECRET)
            + '"}}',
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tool_agent.httpx, "post", go_escaping_401)
    agent = OpenAICompatToolAgent(
        "m",
        "http://localhost/v1",
        extra_headers={"anthropic-workspace-id": GO_ESCAPING_SECRET},
        max_retries=0,
    )

    with pytest.raises(LLMRunnerError) as caught:
        agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    message = str(caught.value)
    assert GO_ESCAPING_SECRET not in message
    assert _go_encoded(GO_ESCAPING_SECRET) not in message
    assert tool_agent.WITHHELD_BODY_MARKER in message
    # The status still names the rejection, so dropping the body does not
    # leave the operator with nothing to act on.
    assert "401" in message


# HTTP forbids CR and LF in a header value, but --model-header only strips
# surrounding whitespace, so an embedded one reaches httpx intact. Built from
# chr() rather than written as escapes so the test states which bytes it means.
CRLF_HEADER_SECRET = "wrkspc" + chr(13) + chr(10) + "secret_9999"


def test_transport_exception_text_does_not_leak_a_header_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leak source that is not a response body: h11 quoting a bad header.

    Verified against a live socket: h11 refuses to serialize the value and
    raises LocalProtocolError("Illegal header value b'wrkspc...'"), whose text
    the harness interpolates straight into LLMRunnerError. Nothing in that path
    touches exc.response, so masking the body alone never reached it.
    """
    quoted = f"Illegal header value {CRLF_HEADER_SECRET.encode()!r}"

    def refusing_post(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.LocalProtocolError(quoted)

    # Precondition: a bytes repr escapes CR and LF, so the plaintext genuinely
    # is absent and a plaintext-only search would pass vacuously.
    escaped = json.dumps(CRLF_HEADER_SECRET)[1:-1]
    assert CRLF_HEADER_SECRET not in quoted
    assert escaped in quoted

    monkeypatch.setattr(tool_agent.httpx, "post", refusing_post)
    agent = OpenAICompatToolAgent(
        "m",
        "http://localhost/v1",
        extra_headers={"anthropic-workspace-id": CRLF_HEADER_SECRET},
        max_retries=0,
    )

    with pytest.raises(LLMRunnerError) as caught:
        agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    message = str(caught.value)
    assert CRLF_HEADER_SECRET not in message
    assert escaped not in message
    assert tool_agent.REDACTION_MARKER in message
    # The summary half is harness-authored, so the operator still learns which
    # endpoint failed and why even though the value was masked out.
    assert "Illegal header value" in message
    assert "http://localhost/v1" in message


def test_error_summary_survives_a_withheld_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Withholding must cost the diagnostic, never the endpoint that failed.

    redact_secrets replaces its whole input when masking provably failed, so
    routing the entire message through it would drop the summary too. This
    pins the split: a transport exception spelling the secret in a form the
    form set cannot enumerate loses the detail and keeps the locator.
    """

    def go_escaping_post(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError('{"seen":"' + _go_encoded(GO_ESCAPING_SECRET) + '"}')

    monkeypatch.setattr(tool_agent.httpx, "post", go_escaping_post)
    agent = OpenAICompatToolAgent(
        "m",
        "http://localhost/v1",
        extra_headers={"anthropic-workspace-id": GO_ESCAPING_SECRET},
        max_retries=0,
    )

    with pytest.raises(LLMRunnerError) as caught:
        agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    message = str(caught.value)
    assert GO_ESCAPING_SECRET not in message
    assert _go_encoded(GO_ESCAPING_SECRET) not in message
    assert tool_agent.WITHHELD_BODY_MARKER in message
    assert "http://localhost/v1" in message


class TestOpenAICompatToolAgent:
    def _agent(self) -> OpenAICompatToolAgent:
        return OpenAICompatToolAgent("qwen3", "http://localhost:11434/v1", max_retries=0)

    def test_request_carries_tools_and_temperature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            captured["body"] = kwargs["json"]
            return _response(
                {
                    "choices": [{"message": {"content": "done", "tool_calls": []}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                }
            )

        monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
        turn = self._agent().propose([UserMessage(text="find redis")], [SEARCH_TOOL])

        assert captured["url"].endswith("/chat/completions")
        body = captured["body"]
        assert body["temperature"] == 0
        assert body["tool_choice"] == "auto"
        assert body["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "search_notes",
                    "description": "Search notes",
                    "parameters": SEARCH_TOOL.input_schema,
                },
            }
        ]
        assert turn.text == "done"
        assert turn.tool_calls == ()
        assert turn.input_tokens == 12
        assert turn.output_tokens == 3

    def test_transcript_maps_assistant_and_tool_roles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kwargs: Any) -> httpx.Response:
            captured["body"] = kwargs["json"]
            return _response(
                {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            )

        monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
        transcript = [
            UserMessage(text="find redis"),
            AssistantTurn(
                text="",
                tool_calls=(
                    ToolCall(call_id="c1", name="search_notes", arguments={"query": "redis"}),
                ),
            ),
            ToolReturn(call_id="c1", name="search_notes", text="2 results", is_error=False),
        ]
        self._agent().propose(transcript, [SEARCH_TOOL])

        messages = captured["body"]["messages"]
        assert messages[0] == {"role": "user", "content": "find redis"}
        assert messages[1]["role"] == "assistant"
        assert messages[1]["tool_calls"][0]["function"]["name"] == "search_notes"
        assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {
            "query": "redis"
        }
        assert messages[2] == {"role": "tool", "tool_call_id": "c1", "content": "2 results"}

    def test_parses_tool_calls_from_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-9",
                                "function": {
                                    "name": "search_notes",
                                    "arguments": '{"query": "redis"}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        monkeypatch.setattr(tool_agent.httpx, "post", lambda url, **kw: _response(payload))
        turn = self._agent().propose([UserMessage(text="go")], [SEARCH_TOOL])

        assert turn.text == ""
        assert turn.tool_calls == (
            ToolCall(call_id="call-9", name="search_notes", arguments={"query": "redis"}),
        )

    def test_malformed_tool_arguments_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c", "function": {"name": "search_notes", "arguments": "{oops"}}
                        ]
                    }
                }
            ]
        }
        monkeypatch.setattr(tool_agent.httpx, "post", lambda url, **kw: _response(payload))
        with pytest.raises(LLMRunnerError, match="malformed tool arguments"):
            self._agent().propose([UserMessage(text="go")], [SEARCH_TOOL])

    def test_missing_usage_block_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Silent 0-token turns would corrupt the headline metric and disarm the
        # tokens budget, so an omitted usage block is an explicit error.
        payload = {"choices": [{"message": {"content": "done"}}]}
        monkeypatch.setattr(tool_agent.httpx, "post", lambda url, **kw: _response(payload))
        with pytest.raises(LLMRunnerError, match="usage"):
            self._agent().propose([UserMessage(text="go")], [SEARCH_TOOL])

    def test_incomplete_usage_block_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 12},
        }
        monkeypatch.setattr(tool_agent.httpx, "post", lambda url, **kw: _response(payload))
        with pytest.raises(LLMRunnerError, match="token counts"):
            self._agent().propose([UserMessage(text="go")], [SEARCH_TOOL])

    def test_retry_exhaustion_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"count": 0}

        def failing_post(url: str, **kwargs: Any) -> httpx.Response:
            calls["count"] += 1
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(tool_agent.httpx, "post", failing_post)
        agent = OpenAICompatToolAgent("m", "http://localhost:1", max_retries=1)
        with pytest.raises(LLMRunnerError, match="after 2 attempts"):
            agent.propose([UserMessage(text="go")], [SEARCH_TOOL])
        assert calls["count"] == 2


class TestScriptedToolAgent:
    def _agent(self) -> ScriptedToolAgent:
        return ScriptedToolAgent(
            script={
                "tasks": {
                    "orphan": [
                        {
                            "tool_calls": [
                                {
                                    "name": "search_notes",
                                    "arguments": {"query": "x", "project": "{project}"},
                                }
                            ]
                        },
                        {"text": "final answer"},
                    ]
                }
            }
        )

    def test_substring_keying_selects_turn_by_transcript_position(self) -> None:
        agent = self._agent()
        first = agent.propose([UserMessage(text="please find the orphan notes")], [])
        assert first.tool_calls[0].name == "search_notes"

        transcript = [
            UserMessage(text="please find the orphan notes"),
            AssistantTurn(text="", tool_calls=first.tool_calls),
            ToolReturn(call_id="c", name="search_notes", text="results", is_error=False),
        ]
        second = agent.propose(transcript, [])
        assert second.text == "final answer"
        assert second.tool_calls == ()

    def test_placeholder_substitution_is_recursive(self) -> None:
        arguments = {"project": "{project}", "nested": {"p": "{project}"}, "n": 3}
        resolved = substitute_placeholders(arguments, {"project": "at-run-task"})
        assert resolved == {"project": "at-run-task", "nested": {"p": "at-run-task"}, "n": 3}

    def test_unmatched_prompt_raises(self) -> None:
        with pytest.raises(LLMRunnerError, match="no entry matching"):
            self._agent().propose([UserMessage(text="something else")], [])

    def test_exhausted_script_raises(self) -> None:
        agent = self._agent()
        transcript = [
            UserMessage(text="orphan"),
            AssistantTurn(text="", tool_calls=()),
            AssistantTurn(text="", tool_calls=()),
        ]
        with pytest.raises(LLMRunnerError, match="exhausted"):
            agent.propose(transcript, [])
