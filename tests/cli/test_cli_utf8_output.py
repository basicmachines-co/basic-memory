"""The CLI writes UTF-8 even where the locale encoding cannot encode a note (#2003)."""

import io
import os
import subprocess
import sys

import pytest

from basic_memory.cli.main import use_utf8_output

ARROW = "←"


def test_cp1252_stream_is_switched_to_utf8() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    with pytest.raises(UnicodeEncodeError):
        stream.write(ARROW)
        stream.flush()

    use_utf8_output(stream)
    stream.write(ARROW)
    stream.flush()

    assert raw.getvalue().endswith(ARROW.encode("utf-8"))


def test_non_text_wrapper_streams_are_left_alone() -> None:
    stream = io.StringIO()
    use_utf8_output(stream)
    stream.write(ARROW)
    assert stream.getvalue() == ARROW


def test_cli_process_with_cp1252_output_prints_utf8() -> None:
    # PYTHONIOENCODING=cp1252 reproduces Windows' piped-stdout encoding anywhere.
    result = subprocess.run(
        [sys.executable, "-c", f"import basic_memory.cli.main; print({ARROW!r})"],
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8").strip() == ARROW
