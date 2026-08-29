"""
Prompt/description hardening tests (#1341, part 1).

These assert the always-on guidance that makes the write path insistent and the
returned permalink citable: the bm_write/bm_edit tool descriptions and the
system_prompt_block wording. This is the durable half of the #1341 fix — the
reply-scanning save-claim guard was dropped as unreliable.
"""

from __future__ import annotations


def test_system_prompt_block_forbids_unwritten_save_reports(bm):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = "test-proj"
    p._mode = "local"
    out = p.system_prompt_block()
    assert "Never say information was saved" in out
    assert "permalink" in out


def test_bm_write_description_is_insistent(bm):
    schema = next(s for s in bm.TOOL_SCHEMAS if s["name"] == "bm_write")
    assert "remember, record, save, or note" in schema["description"]
    assert "permalink" in schema["description"]


def test_bm_edit_description_mentions_permalink(bm):
    schema = next(s for s in bm.TOOL_SCHEMAS if s["name"] == "bm_edit")
    assert "permalink" in schema["description"]
