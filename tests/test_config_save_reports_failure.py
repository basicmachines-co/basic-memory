"""A swallowed config write must still tell the caller it did not happen (#1440 review).

`save_basic_memory_config` catches every exception so that best-effort callers --
recording that a promo was shown, stamping an auto-update check -- do not fail a
command over a config write. That also turned a failed write into a silent
success for callers that must know, which made `bm project add`'s recovery path
for exactly this failure unreachable in production.

These exercise the real function, so they fail if the swallow ever goes back to
reporting nothing.
"""

from pathlib import Path

from basic_memory.config import BasicMemoryConfig, save_basic_memory_config


def test_a_failed_write_returns_the_error(tmp_path: Path):
    """A genuine filesystem failure, with nothing patched.

    The parent of the config file is a regular file, so creating the directory
    raises inside the function and is swallowed by the real handler.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    config_file = blocker / "config.json"

    write_error = save_basic_memory_config(config_file, BasicMemoryConfig())

    assert write_error is not None, (
        "the write failed and the function reported success; a caller cannot "
        "tell whether its config was persisted"
    )
    assert not config_file.exists()


def test_a_successful_write_reports_no_error(tmp_path: Path):
    """The success path must stay indistinguishable from before."""
    config_file = tmp_path / "config" / "config.json"

    write_error = save_basic_memory_config(config_file, BasicMemoryConfig())

    assert write_error is None
    assert config_file.exists()


def test_the_manager_passes_the_outcome_through(tmp_path: Path, monkeypatch):
    """`ConfigManager.save_config` is the boundary callers actually use."""
    from basic_memory import config as config_module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / ".basic-memory"))
    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    manager = config_module.ConfigManager()
    assert manager.save_config(BasicMemoryConfig()) is None

    # A real failure inside the atomic replace, so the error travels the whole
    # way through the swallow rather than being raised past it.
    def _refuse(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(config_module.os, "replace", _refuse)

    write_error = manager.save_config(BasicMemoryConfig())

    assert isinstance(write_error, OSError)
