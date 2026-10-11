"""Tests for scripts/publish_skills_release.py.

The web app links to these release assets by name, so a publish that fails
partway must leave every live download in place.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_skills_release.py"
spec = importlib.util.spec_from_file_location("publish_skills_release", SCRIPT)
assert spec is not None and spec.loader is not None
publish_skills_release = importlib.util.module_from_spec(spec)
sys.modules["publish_skills_release"] = publish_skills_release
spec.loader.exec_module(publish_skills_release)

STAGING = publish_skills_release.STAGING_PREFIX
RETIRED = publish_skills_release.RETIRED_PREFIX
CONTRACT = publish_skills_release.CONTRACT_ASSETS


class Killed(BaseException):
    """The runner dying mid-call (a cancelled job): no handler gets to run."""


class FakeRelease:
    """An in-memory release whose operations can be made to fail."""

    def __init__(
        self,
        assets: dict[str, str],
        fail_on: tuple[str, str] | None = None,
        failure: type[BaseException] = RuntimeError,
    ) -> None:
        self.assets = dict(assets)  # name -> content
        self.fail_on = fail_on  # (operation, asset name)
        self.failure = failure

    def _maybe_fail(self, operation: str, name: str) -> None:
        if self.fail_on == (operation, name):
            raise self.failure(f"{operation} {name} failed")

    def asset_names(self) -> list[str]:
        return sorted(self.assets)

    def upload(self, path: Path, name: str) -> None:
        self._maybe_fail("upload", name)
        assert name not in self.assets, f"upload would clobber {name}"
        self.assets[name] = path.read_text()

    def rename(self, old: str, new: str) -> None:
        self._maybe_fail("rename", old)
        assert new not in self.assets, f"rename would clobber {new}"
        self.assets[new] = self.assets.pop(old)

    def delete(self, name: str) -> None:
        self._maybe_fail("delete", name)
        del self.assets[name]


@pytest.fixture
def archives(tmp_path: Path) -> dict[str, Path]:
    built = {}
    for name in (*CONTRACT, "memory-notes.zip"):
        path = tmp_path / name
        path.write_text(f"new {name}")
        built[name] = path
    return built


def _live(names: list[str], label: str) -> dict[str, str]:
    return {name: f"{label} {name}" for name in names}


def test_publish_replaces_assets_and_prunes_stale_ones(archives: dict[str, Path]) -> None:
    release = FakeRelease(
        {
            **_live([*CONTRACT, "memory-removed.zip"], "old"),
            # Left by a run that died midway.
            f"{STAGING}memory-notes.zip": "half-uploaded",
            f"{RETIRED}basic-memory.zip": "orphan",
        }
    )

    publish_skills_release.publish(release, archives)

    assert release.assets == {name: f"new {name}" for name in archives}


@pytest.mark.parametrize(
    "fail_on",
    [
        ("upload", f"{STAGING}basic-memory.zip"),
        ("upload", f"{STAGING}memory-onboarding.zip"),
        ("rename", "basic-memory-chatgpt.zip"),
    ],
)
def test_failed_publish_keeps_every_live_download(
    archives: dict[str, Path], fail_on: tuple[str, str]
) -> None:
    live = _live(list(CONTRACT), "old")
    release = FakeRelease(live, fail_on=fail_on)

    with pytest.raises(RuntimeError):
        publish_skills_release.publish(release, archives)

    # Each contract name still serves either the old or the new archive.
    for name in CONTRACT:
        assert release.assets.get(name) in {f"old {name}", f"new {name}"}


def test_a_failed_upload_changes_nothing_live(archives: dict[str, Path]) -> None:
    live = _live(list(CONTRACT), "old")
    release = FakeRelease(live, fail_on=("upload", f"{STAGING}memory-onboarding.zip"))

    with pytest.raises(RuntimeError):
        publish_skills_release.publish(release, archives)

    assert {name: release.assets[name] for name in CONTRACT} == live


def test_next_run_recovers_from_an_interrupted_one(archives: dict[str, Path]) -> None:
    release = FakeRelease(_live(list(CONTRACT), "old"), fail_on=("rename", "memory-onboarding.zip"))
    with pytest.raises(RuntimeError):
        publish_skills_release.publish(release, archives)

    release.fail_on = None
    publish_skills_release.publish(release, archives)

    assert release.assets == {name: f"new {name}" for name in archives}


def test_a_failed_staged_rename_puts_the_old_asset_back(archives: dict[str, Path]) -> None:
    # GitHub rejects the second rename after the live asset was moved aside.
    release = FakeRelease(
        _live(list(CONTRACT), "old"), fail_on=("rename", f"{STAGING}basic-memory.zip")
    )
    with pytest.raises(RuntimeError):
        publish_skills_release.publish(release, archives)
    assert release.assets["basic-memory.zip"] == "old basic-memory.zip"
    assert f"{RETIRED}basic-memory.zip" not in release.assets


def test_next_run_restores_an_asset_left_renamed_aside(archives: dict[str, Path]) -> None:
    # The first run is killed on its second rename, so no handler runs:
    # basic-memory.zip is now only retired--basic-memory.zip, and the URL is empty.
    release = FakeRelease(
        _live(list(CONTRACT), "old"),
        fail_on=("rename", f"{STAGING}basic-memory.zip"),
        failure=Killed,
    )
    with pytest.raises(Killed):
        publish_skills_release.publish(release, archives)
    assert "basic-memory.zip" not in release.assets

    # The next run fails too, at an upload. Recovery must already have put the
    # old archive back rather than deleting it as a leftover.
    release.fail_on = ("upload", f"{STAGING}memory-onboarding.zip")
    release.failure = RuntimeError
    with pytest.raises(RuntimeError):
        publish_skills_release.publish(release, archives)
    assert release.assets["basic-memory.zip"] == "old basic-memory.zip"

    release.fail_on = None
    publish_skills_release.publish(release, archives)
    assert release.assets == {name: f"new {name}" for name in archives}


def test_archive_names_enforce_the_contract(tmp_path: Path) -> None:
    def paths(*names: str, folder: str = "a") -> list[Path]:
        return [tmp_path / folder / name for name in names]

    assert set(publish_skills_release.archive_names(paths(*CONTRACT))) == set(CONTRACT)
    with pytest.raises(SystemExit, match="Duplicate asset name"):
        publish_skills_release.archive_names(
            paths(*CONTRACT) + paths("basic-memory.zip", folder="b")
        )
    with pytest.raises(SystemExit, match="Missing contract assets: memory-onboarding.zip"):
        publish_skills_release.archive_names(paths("basic-memory.zip", "basic-memory-chatgpt.zip"))
    with pytest.raises(SystemExit, match="reserved prefix"):
        publish_skills_release.archive_names(paths(*CONTRACT, f"{STAGING}x.zip"))
    with pytest.raises(SystemExit, match="No archives"):
        publish_skills_release.archive_names([])
