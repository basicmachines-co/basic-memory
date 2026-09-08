"""A save confirmation must not borrow tool evidence from another turn."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


_spec = importlib.util.spec_from_file_location(
    "bm_save_claims", Path(__file__).parents[1] / "save_claims.py"
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)


def test_guard_correlates_successful_writes_with_current_turn() -> None:
    guard = _module.SaveClaimGuard()
    guard.begin_turn(session_id="a", turn_id="1", user_message="Remember this")
    guard.begin_turn(session_id="b", turn_id="1", user_message="Remember this")
    guard.observe_tool(session_id="a", turn_id="1", tool_name="bm_write", status="ok")
    assert guard.transform(session_id="a", response_text="Saved to Basic Memory.") is None
    assert "unverified" in guard.transform(session_id="b", response_text="Saved to Basic Memory.")
    guard.begin_turn(session_id="a", turn_id="2", user_message="Remember this too")
    guard.observe_tool(session_id="a", turn_id="1", tool_name="bm_write", status="ok")
    guard.observe_tool(session_id="a", turn_id="2", tool_name="bm_edit", status="error")
    assert "unverified" in guard.transform(session_id="a", response_text="I've saved it.")


def test_guard_ignores_examples_and_non_claims() -> None:
    for response in [
        "I will save it.",
        "I have not saved it.",
        "> Saved to Basic Memory.",
        "```text\nSaved to Basic Memory.\n```",
        "Saved the image to disk.",
    ]:
        guard = _module.SaveClaimGuard()
        guard.begin_turn(session_id="a", turn_id="1", user_message="Hello")
        assert guard.transform(session_id="a", response_text=response) is None
    guard = _module.SaveClaimGuard()
    guard.begin_turn(session_id="a", turn_id="1", user_message="Remember this")
    guard.end_session(session_id="a")
    assert guard.transform(session_id="a", response_text="Saved it.") is None


def test_generic_file_save_does_not_imply_basic_memory() -> None:
    guard = _module.SaveClaimGuard()
    guard.begin_turn(session_id="a", turn_id="1", user_message="Save the image to disk")
    assert guard.transform(session_id="a", response_text="Saved the image to disk.") is None
    guard.begin_turn(session_id="a", turn_id="2", user_message="Save this to Basic Memory")
    assert "unverified" in guard.transform(session_id="a", response_text="I've saved it.")


def test_memory_request_does_not_turn_a_denial_into_a_save_claim() -> None:
    for response in [
        "I have saved nothing to Basic Memory.",
        "Saved no notes to Basic Memory.",
        "I recorded none of this in Basic Memory.",
        "I stored zero notes.",
        "I updated my response, but did not save it.",
        "I added a suggestion to my response, not a note.",
        "I saved it locally, but did not store it in Basic Memory.",
        "Saved? No, I did not.",
    ]:
        guard = _module.SaveClaimGuard()
        guard.begin_turn(session_id="a", turn_id="1", user_message="Remember this")
        assert guard.transform(session_id="a", response_text=response) is None


def test_missing_turn_identity_does_not_create_evidence() -> None:
    guard = _module.SaveClaimGuard()
    guard.begin_turn(session_id="a", user_message="Remember this")
    assert guard.transform(session_id="a", response_text="Saved to Basic Memory.") is None


def test_only_the_save_clause_supplies_qualifications() -> None:
    for response in [
        "No problem, I've saved it to Basic Memory.",
        "I've saved it. Do you need anything else?",
        "Sure, I saved it in Basic Memory.",
        "Done — I've recorded it.",
        "I've saved it to Basic Memory; no other settings were changed.",
        "I've saved it, no problem.",
    ]:
        assert _module.claims_memory_save(response, memory_requested=True)
    assert not _module.claims_memory_save("Saved the image to disk.", memory_requested=True)
    for response in [
        "I saved it to Basic Memory yesterday.",
        "Saved the photo to Google Drive.",
        "Stored it in Dropbox.",
        "Sure, I saved it to Google Drive.",
    ]:
        assert not _module.claims_memory_save(response, memory_requested=True)


def test_reported_hermes_acknowledgment_is_corrected() -> None:
    guard = _module.SaveClaimGuard()
    guard.begin_turn(
        session_id="a", turn_id="1", user_message="Remember this: the renewal date is October 3rd."
    )
    response = "Got it. I have recorded that:\n- The renewal date is October 3rd."
    corrected = guard.transform(session_id="a", response_text=response)
    assert corrected.startswith(response)
    assert "unverified" in corrected


def test_registered_hooks_follow_configured_provider(bm, monkeypatch) -> None:
    config = {"memory": {"provider": "basic-memory"}}
    config_module = ModuleType("hermes_cli.config")
    config_module.load_config_readonly = lambda: config
    config_module.cfg_get = lambda value, *keys: value[keys[0]][keys[1]]
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config_module)
    hooks = {}
    ctx = SimpleNamespace(
        register_memory_provider=lambda provider: None,
        register_hook=lambda name, callback: hooks.setdefault(name, callback),
        register_command=lambda *args, **kwargs: None,
        register_skill=lambda *args, **kwargs: None,
    )
    bm.register(ctx)
    hooks["pre_llm_call"](session_id="a", turn_id="1", user_message="Remember this")
    assert "unverified" in hooks["transform_llm_output"](
        session_id="a", response_text="Saved to Basic Memory."
    )
    hooks["pre_llm_call"](session_id="a", turn_id="2", user_message="Remember this")
    config["memory"]["provider"] = "builtin"
    hooks["pre_llm_call"](session_id="a", turn_id="3", user_message="Remember this")
    assert (
        hooks["transform_llm_output"](session_id="a", response_text="Saved to Basic Memory.")
        is None
    )
