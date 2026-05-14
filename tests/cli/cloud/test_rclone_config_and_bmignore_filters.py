import time

from basic_memory.cli.commands.cloud.bisync_commands import convert_bmignore_to_rclone_filters
from basic_memory.cli.commands.cloud.rclone_config import (
    configure_rclone_remote,
    get_rclone_config_path,
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
    # Each pattern emits both a direct-match and a recursive-contents rule
    assert "- node_modules" in content
    assert "- node_modules/**" in content
    assert "- *.pyc" in content
    assert "- *.pyc/**" in content
    assert "- .git" in content
    assert "- .git/**" in content


def test_convert_bmignore_file_pattern_not_mangled_as_directory(config_home):
    """Regression: config.json must be excluded as a FILE, not as a dir content glob.

    Previously `config.json` (no wildcard) became `- config.json/**`, which only
    excludes contents of a *directory* named config.json, leaving the actual file
    unfiltered during bisync.
    """
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text("config.json\n", encoding="utf-8")

    content = convert_bmignore_to_rclone_filters().read_text(encoding="utf-8").splitlines()

    # Must match the file itself
    assert "- config.json" in content
    # Also emit the /** form for completeness (covers any hypothetical dir of that name)
    assert "- config.json/**" in content


def test_convert_bmignore_hidden_glob_covers_directory_contents(config_home):
    """Regression: `.*` must exclude hidden directory *contents*, not just hidden files.

    Previously `.*` became `- .*` only. rclone globs don't cross `/`, so
    `.obsidian/workspace.json` would not be matched by `- .*` alone.
    """
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text(".*\n", encoding="utf-8")

    content = convert_bmignore_to_rclone_filters().read_text(encoding="utf-8").splitlines()

    # Must exclude hidden files at any level
    assert "- .*" in content
    # Must also exclude contents of hidden directories
    assert "- .*/**" in content


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
