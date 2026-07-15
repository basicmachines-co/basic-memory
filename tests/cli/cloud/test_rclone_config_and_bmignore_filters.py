import os
import time

import pytest

from basic_memory.cli.commands.cloud.bisync_commands import (
    convert_bmignore_to_rclone_filters,
    convert_bmignore_to_rclone_prune_filters,
)
from basic_memory.cli.commands.cloud.rclone_config import (
    RcloneConfigError,
    configure_rclone_remote,
    get_rclone_config_path,
    rclone_remote_exists,
    remote_name_for_workspace,
)
from basic_memory.ignore_utils import get_bmignore_path


def test_convert_bmignore_to_rclone_filters_creates_and_converts(config_home):
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "node_modules",
                "*.pyc",
                ".git",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rclone_filter = convert_bmignore_to_rclone_filters()
    assert rclone_filter.exists()
    content = rclone_filter.read_text(encoding="utf-8").splitlines()

    # Comments/empties preserved
    assert "# comment" in content
    assert "" in content
    # Plain and wildcard patterns exclude direct matches and recursive contents.
    assert "- node_modules" in content
    assert "- node_modules/**" in content
    assert "- *.pyc" in content
    assert "- *.pyc/**" in content
    assert "- .git" in content
    assert "- .git/**" in content


def test_convert_bmignore_to_rclone_filters_excludes_files_and_hidden_directory_contents(
    config_home,
):
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text("config.json\n.*\nnode_modules/**\n", encoding="utf-8")

    rclone_filter = convert_bmignore_to_rclone_filters()
    content = rclone_filter.read_text(encoding="utf-8").splitlines()

    assert "- config.json" in content
    assert "- config.json/**" in content
    assert "- .*" in content
    assert "- .*/**" in content
    assert "- node_modules" in content
    assert "- node_modules/**" in content


def test_convert_bmignore_to_rclone_filters_preserves_directory_only_patterns(config_home):
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text("cache/\nconfig.json/**\n", encoding="utf-8")

    rclone_filter = convert_bmignore_to_rclone_filters()
    content = rclone_filter.read_text(encoding="utf-8").splitlines()

    assert "- cache/" in content
    assert "- cache/**" in content
    assert "- cache" not in content
    assert "- config.json" in content
    assert "- config.json/**" in content


def test_convert_bmignore_to_rclone_filters_is_cached_when_up_to_date(config_home):
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text("node_modules\n", encoding="utf-8")

    first = convert_bmignore_to_rclone_filters()
    first_mtime = first.stat().st_mtime

    # Ensure bmignore is older than rclone filter file
    time.sleep(0.01)
    # Touch rclone filter to be "newer"
    first.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

    second = convert_bmignore_to_rclone_filters()
    assert second == first
    assert second.stat().st_mtime >= first_mtime


def test_convert_bmignore_to_prune_filters_inverts_patterns(config_home):
    """Prune filter includes exactly what the sync filter excludes (#1032)."""
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "node_modules",
                "*.pyc",
                "secrets/**",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    prune_filter = convert_bmignore_to_rclone_prune_filters()
    assert prune_filter.name == ".bmignore.rclone-prune"
    content = prune_filter.read_text(encoding="utf-8").splitlines()

    # Comments/empties preserved
    assert "# comment" in content
    assert "" in content
    # Each ignore pattern becomes an include for the direct match and children.
    assert "+ node_modules" in content
    assert "+ node_modules/**" in content
    assert "+ *.pyc" in content
    assert "+ *.pyc/**" in content
    assert "+ secrets" in content
    assert "+ secrets/**" in content
    # The exclude-all terminator protects everything else, and must come last.
    assert content[-1] == "- *"


def test_convert_bmignore_to_prune_filters_directory_only_pattern(config_home):
    """Directory-only patterns invert to just the contents rule.

    A bare `+ cache` would also select a same-named *file* that the
    directory-only exclude (`- cache/`) never hid from sync.
    """
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text("cache/\n", encoding="utf-8")

    prune_filter = convert_bmignore_to_rclone_prune_filters()
    content = prune_filter.read_text(encoding="utf-8").splitlines()

    assert "+ cache/**" in content
    assert "+ cache" not in content
    assert content[-1] == "- *"


def test_convert_bmignore_to_prune_filters_always_regenerates(config_home):
    """No mtime caching: a stale filter in front of rclone delete is a hazard."""
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text("old-pattern\n", encoding="utf-8")

    prune_filter = convert_bmignore_to_rclone_prune_filters()
    assert "+ old-pattern" in prune_filter.read_text(encoding="utf-8")

    # Update .bmignore but make the generated file look newer — the mtime-cached
    # exclude converter would skip regeneration here; prune must not.
    bmignore.write_text("new-pattern\n", encoding="utf-8")
    future = time.time() + 3600
    os.utime(prune_filter, (future, future))

    regenerated = convert_bmignore_to_rclone_prune_filters()
    content = regenerated.read_text(encoding="utf-8")
    assert "+ new-pattern" in content
    assert "+ old-pattern" not in content


def test_configure_rclone_remote_writes_config_and_backs_up_existing(config_home):
    cfg_path = get_rclone_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("[other]\ntype = local\n", encoding="utf-8")

    remote = configure_rclone_remote(access_key="ak", secret_key="sk")
    assert remote == "basic-memory-cloud"

    # Config file updated
    text = cfg_path.read_text(encoding="utf-8")
    assert "[basic-memory-cloud]" in text
    assert "type = s3" in text
    assert "access_key_id = ak" in text
    assert "secret_access_key = sk" in text
    assert "encoding = Slash,InvalidUtf8" in text

    # Backup exists
    backups = list(cfg_path.parent.glob("rclone.conf.backup-*"))
    assert backups, "expected a backup of rclone.conf to be created"


def test_remote_name_for_workspace():
    # Default workspace keeps the legacy remote name (back-compat)
    assert remote_name_for_workspace("personal", is_default=True) == "basic-memory-cloud"
    assert remote_name_for_workspace(None, is_default=False) == "basic-memory-cloud"
    # Non-default workspaces get their own tenant-scoped remote
    assert remote_name_for_workspace("acme", is_default=False) == "basic-memory-cloud-acme"


def test_remote_name_for_workspace_rejects_unsafe_slug():
    # A slug from the API with characters invalid in an rclone remote name must
    # fail fast rather than write a broken rclone.conf section.
    for bad in ["a/b", "has space", "dot.dot", "weird:name"]:
        with pytest.raises(RcloneConfigError):
            remote_name_for_workspace(bad, is_default=False)


def test_configure_rclone_remote_named_workspace_remote(config_home):
    remote = configure_rclone_remote(
        access_key="ak", secret_key="sk", remote_name="basic-memory-cloud-acme"
    )
    assert remote == "basic-memory-cloud-acme"

    text = get_rclone_config_path().read_text(encoding="utf-8")
    assert "[basic-memory-cloud-acme]" in text
    assert "access_key_id = ak" in text


def test_rclone_remote_exists(config_home):
    assert rclone_remote_exists("basic-memory-cloud-acme") is False
    configure_rclone_remote(access_key="ak", secret_key="sk", remote_name="basic-memory-cloud-acme")
    assert rclone_remote_exists("basic-memory-cloud-acme") is True
