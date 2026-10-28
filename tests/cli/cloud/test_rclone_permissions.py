"""Credential permissions must not depend on the caller's umask (#1914)."""

import os
import stat
from pathlib import Path

import pytest

from basic_memory.cli.commands.cloud.rclone_config import (
    configure_rclone_remote,
    get_rclone_config_path,
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("existing", [False, True])
def test_rclone_credentials_are_private(config_home: Path, existing: bool):
    config_path = config_home / ".config" / "rclone" / "rclone.conf"
    backup = config_path.with_suffix(f".conf.backup-{os.getpid()}")
    if existing:
        config_path.parent.mkdir(parents=True)
        config_path.parent.chmod(0o755)
        config_path.write_text("[other]\ntype = local\n")
        config_path.chmod(0o644)
        backup.write_text("old backup")
        backup.chmod(0o644)

    previous_umask = os.umask(0)
    try:
        configure_rclone_remote("test-access", "test-secret")
    finally:
        os.umask(previous_umask)

    assert get_rclone_config_path() == config_path
    assert stat.S_IMODE(config_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert "secret_access_key = test-secret" in config_path.read_text()
    if existing:
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert backup.read_text() == "[other]\ntype = local\n"
        assert "[other]" in config_path.read_text()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_malformed_existing_config_is_tightened_before_parsing(config_home: Path):
    import configparser

    config_path = get_rclone_config_path()
    config_path.write_text("secret_access_key = old-secret\n")
    config_path.chmod(0o644)
    with pytest.raises(configparser.MissingSectionHeaderError):
        configure_rclone_remote("test-access", "test-secret")
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert config_path.read_text() == "secret_access_key = old-secret\n"
