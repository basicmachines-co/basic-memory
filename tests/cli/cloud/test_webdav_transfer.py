"""Tests for the WebDAV push/pull engine used on Team workspaces (#1262).

These exercise the real comparison and transfer code against a mocked WebDAV
surface, so every `--on-conflict` strategy is proven to behave the way it does on
the Personal (rclone) path.
"""

import hashlib
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from basic_memory.cli.commands.cloud.transfer import TransferPlan
from basic_memory.cli.commands.cloud.webdav import RemoteFile, WebdavError
from basic_memory.cli.commands.cloud.webdav_transfer import (
    build_transfer_plan,
    scan_local_files,
    webdav_project_diff,
    webdav_project_transfer,
)
from basic_memory.ignore_utils import load_gitignore_patterns

MODIFIED = datetime(2026, 6, 8, 10, 30, tzinfo=timezone.utc)


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _write(root: Path, rel_path: str, content: str, *, mtime: float | None = None) -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# Sentinel for "derive the entity tag from the content", so an explicit None can
# still mean "the service reported no entity tag at all".
_DERIVE_ETAG = "derive"


def _remote(path: str, content: str, *, etag: str | None = _DERIVE_ETAG, modified=MODIFIED):
    data = content.encode("utf-8")
    return RemoteFile(
        path=path,
        size=len(data),
        etag=_md5(data) if etag == _DERIVE_ETAG else etag,
        modified=modified,
    )


def _plain(text: str) -> str:
    """Strip the console's styling so assertions read against the words alone."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _client_factory(handler):
    @asynccontextmanager
    async def factory():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://cloud.example.test"
        ) as client:
            yield client

    return factory


# --- Planning ---


def test_plan_classifies_new_conflicting_and_destination_only_files(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "same.md", "identical")
    _write(root, "diverged.md", "local version")
    _write(root, "local-only.md", "mine")

    remote_files = [
        _remote("same.md", "identical"),
        _remote("diverged.md", "cloud version!"),
        _remote("cloud-only.md", "theirs"),
    ]

    plan = build_transfer_plan(local_root=root, remote_files=remote_files, direction="pull")

    assert plan.new == ["cloud-only.md"]
    assert plan.conflicts == ["diverged.md"]
    assert plan.dest_only == ["local-only.md"]
    assert plan.errors == []


def test_plan_direction_flips_which_side_is_the_source(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "local-only.md", "mine")
    remote_files = [_remote("cloud-only.md", "theirs")]

    plan = build_transfer_plan(local_root=root, remote_files=remote_files, direction="push")

    assert plan.new == ["local-only.md"]
    assert plan.dest_only == ["cloud-only.md"]


def test_plan_treats_a_size_difference_as_a_conflict_without_hashing(config_home, tmp_path):
    """Size settles it, so an unusable entity tag never even gets consulted."""
    root = tmp_path / "research"
    _write(root, "a.md", "short")
    remote_files = [RemoteFile(path="a.md", size=999, etag="opaque", modified=None)]

    plan = build_transfer_plan(local_root=root, remote_files=remote_files, direction="pull")

    assert plan.conflicts == ["a.md"]
    assert plan.errors == []


def test_plan_ignores_bmignore_paths_on_both_sides(config_home, tmp_path):
    """An ignored path is invisible in both listings, as rclone's filter makes it."""
    root = tmp_path / "research"
    _write(root, ".hidden.md", "local hidden")
    _write(root, "keep.md", "keep")

    remote_files = [_remote("keep.md", "keep"), _remote(".hidden.md", "cloud hidden")]

    plan = build_transfer_plan(local_root=root, remote_files=remote_files, direction="pull")

    assert plan.new == []
    assert plan.conflicts == []
    assert plan.dest_only == []


def test_scan_local_files_prunes_ignored_directories(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "notes/a.md", "a")
    _write(root, ".git/config", "nope")

    patterns = load_gitignore_patterns(root, use_gitignore=False)
    assert sorted(scan_local_files(root, patterns)) == ["notes/a.md"]


def test_plan_rejects_a_cloud_path_that_escapes_the_project(config_home, tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    remote_files = [RemoteFile(path="../escape.md", size=1, etag=None, modified=MODIFIED)]

    with pytest.raises(WebdavError, match="outside the project"):
        build_transfer_plan(local_root=root, remote_files=remote_files, direction="pull")


# --- Comparison fallback when the entity tag cannot be a content hash ---


@pytest.mark.parametrize(
    "etag",
    [
        None,  # server sent no validator
        "d41d8cd98f00b204e9800998ecf8427e-4",  # multipart digest-of-digests
        "W/d41d8cd98f00b204e9800998ecf8427e",  # weak validator
    ],
)
def test_matching_size_and_timestamp_is_a_match_without_a_usable_etag(config_home, tmp_path, etag):
    root = tmp_path / "research"
    _write(root, "a.md", "same size", mtime=MODIFIED.timestamp())
    remote_files = [_remote("a.md", "same size", etag=etag)]

    plan = build_transfer_plan(local_root=root, remote_files=remote_files, direction="pull")

    assert plan.conflicts == []
    assert plan.errors == []


def test_a_diverged_timestamp_is_a_conflict_without_a_usable_etag(config_home, tmp_path):
    """Erring toward a conflict is recoverable; a silent skip would lose an edit."""
    root = tmp_path / "research"
    _write(root, "a.md", "same size", mtime=MODIFIED.timestamp() + 600)
    remote_files = [_remote("a.md", "same size", etag=None)]

    plan = build_transfer_plan(local_root=root, remote_files=remote_files, direction="pull")

    assert plan.conflicts == ["a.md"]
    assert plan.errors == []


def test_sub_second_clock_drift_is_still_a_match(config_home, tmp_path):
    """HTTP-date has one-second resolution, so a fractional mtime still matches."""
    root = tmp_path / "research"
    _write(root, "a.md", "same size", mtime=MODIFIED.timestamp() + 0.75)
    remote_files = [_remote("a.md", "same size", etag=None)]

    plan = build_transfer_plan(local_root=root, remote_files=remote_files, direction="pull")

    assert plan.conflicts == []


def test_no_etag_and_no_timestamp_is_reported_as_uncomparable(config_home, tmp_path):
    """With nothing to compare, the file goes to errors — never a silent match."""
    root = tmp_path / "research"
    _write(root, "a.md", "same size")
    remote_files = [RemoteFile(path="a.md", size=len("same size"), etag=None, modified=None)]

    plan = build_transfer_plan(local_root=root, remote_files=remote_files, direction="pull")

    assert plan.errors == ["a.md"]
    assert plan.conflicts == []


# --- Transfers ---


@pytest.mark.asyncio
async def test_pull_downloads_new_files_and_carries_the_cloud_timestamp(config_home, tmp_path):
    root = tmp_path / "research"
    root.mkdir()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/webdav/research/notes/new.md"
        return httpx.Response(
            200,
            content=b"from cloud",
            headers={"Last-Modified": "Mon, 08 Jun 2026 10:30:00 GMT"},
        )

    plan = TransferPlan(new=["notes/new.md"])
    await webdav_project_transfer(
        "research",
        root,
        "pull",
        plan,
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    target = root / "notes" / "new.md"
    assert target.read_bytes() == b"from cloud"
    # rclone preserves modtimes across a transfer; so does this, which is what
    # keeps the timestamp fallback in _compare meaningful after a pull.
    assert target.stat().st_mtime == pytest.approx(MODIFIED.timestamp())
    # The atomic write leaves nothing behind.
    assert sorted(p.name for p in (root / "notes").iterdir()) == ["new.md"]


@pytest.mark.asyncio
async def test_pull_default_never_overwrites_an_existing_local_file(config_home, tmp_path):
    """With no conflicts to resolve, only new files move — the additive contract."""
    root = tmp_path / "research"
    _write(root, "kept.md", "local wins")

    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, content=b"from cloud")

    plan = TransferPlan(new=["new.md"], conflicts=["kept.md"])
    await webdav_project_transfer(
        "research",
        root,
        "pull",
        plan,
        workspace_id="team-tenant",
        strategy="keep-local",
        client_cm_factory=_client_factory(handler),
    )

    assert requested == ["/webdav/research/new.md"]
    assert (root / "kept.md").read_text() == "local wins"


@pytest.mark.asyncio
async def test_pull_keep_cloud_overwrites_the_conflicting_local_file(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "dup.md", "local version")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"cloud version")

    plan = TransferPlan(conflicts=["dup.md"])
    await webdav_project_transfer(
        "research",
        root,
        "pull",
        plan,
        workspace_id="team-tenant",
        strategy="keep-cloud",
        client_cm_factory=_client_factory(handler),
    )

    assert (root / "dup.md").read_text() == "cloud version"


@pytest.mark.asyncio
async def test_pull_keep_both_writes_the_incoming_copy_beside_the_local_one(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "notes/dup.md", "local version")

    async def handler(request: httpx.Request) -> httpx.Response:
        # keep-both fetches the conflicting file under its real name and lands it
        # under the conflict name, so nothing is lost on either side.
        assert request.url.path == "/webdav/research/notes/dup.md"
        return httpx.Response(200, content=b"cloud version")

    plan = TransferPlan(conflicts=["notes/dup.md"])
    await webdav_project_transfer(
        "research",
        root,
        "pull",
        plan,
        workspace_id="team-tenant",
        strategy="keep-both",
        conflict_suffix="20260608-1030",
        client_cm_factory=_client_factory(handler),
    )

    assert (root / "notes" / "dup.md").read_text() == "local version"
    conflict_copy = root / "notes" / "dup.conflict-20260608-1030.md"
    assert conflict_copy.read_text() == "cloud version"


@pytest.mark.asyncio
async def test_push_uploads_new_files_with_their_local_mtime(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "notes/new.md", "local content", mtime=1780000000)

    seen: list[tuple[str, bytes, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.content, request.headers["X-OC-Mtime"]))
        return httpx.Response(201)

    plan = TransferPlan(new=["notes/new.md"])
    await webdav_project_transfer(
        "research",
        root,
        "push",
        plan,
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert seen == [("/webdav/research/notes/new.md", b"local content", "1780000000")]


@pytest.mark.asyncio
async def test_push_keep_local_overwrites_the_conflicting_cloud_file(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "dup.md", "local wins")

    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(204)

    plan = TransferPlan(conflicts=["dup.md"])
    await webdav_project_transfer(
        "research",
        root,
        "push",
        plan,
        workspace_id="team-tenant",
        strategy="keep-local",
        client_cm_factory=_client_factory(handler),
    )

    assert seen == ["/webdav/research/dup.md"]


@pytest.mark.asyncio
async def test_push_keep_cloud_leaves_the_conflicting_cloud_file_alone(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "dup.md", "local version")
    _write(root, "new.md", "new")

    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(201)

    plan = TransferPlan(new=["new.md"], conflicts=["dup.md"])
    await webdav_project_transfer(
        "research",
        root,
        "push",
        plan,
        workspace_id="team-tenant",
        strategy="keep-cloud",
        client_cm_factory=_client_factory(handler),
    )

    assert seen == ["/webdav/research/new.md"]


@pytest.mark.asyncio
async def test_push_keep_both_uploads_the_incoming_copy_under_a_conflict_name(
    config_home, tmp_path
):
    root = tmp_path / "research"
    _write(root, "notes/dup.md", "local version")

    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(201)

    plan = TransferPlan(conflicts=["notes/dup.md"])
    await webdav_project_transfer(
        "research",
        root,
        "push",
        plan,
        workspace_id="team-tenant",
        strategy="keep-both",
        conflict_suffix="20260608-1030",
        client_cm_factory=_client_factory(handler),
    )

    # The cloud's own copy is untouched; the local version lands beside it.
    assert seen == ["/webdav/research/notes/dup.conflict-20260608-1030.md"]


@pytest.mark.asyncio
async def test_transfer_dry_run_moves_nothing(config_home, tmp_path, capsys):
    root = tmp_path / "research"
    root.mkdir()

    async def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("dry run must not touch the network")

    plan = TransferPlan(new=["a.md"], conflicts=["dup.md"])
    await webdav_project_transfer(
        "research",
        root,
        "pull",
        plan,
        workspace_id="team-tenant",
        strategy="keep-both",
        conflict_suffix="S",
        dry_run=True,
        client_cm_factory=_client_factory(handler),
    )

    output = " ".join(_plain(capsys.readouterr().out).split())
    assert "2 file(s) would be transferred" in output
    assert "dup.md -> dup.conflict-S.md" in output
    assert not list(root.iterdir())


@pytest.mark.asyncio
async def test_transfer_reports_when_there_is_nothing_to_do(config_home, tmp_path, capsys):
    root = tmp_path / "research"
    root.mkdir()

    async def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("nothing to transfer must not touch the network")

    await webdav_project_transfer(
        "research",
        root,
        "pull",
        TransferPlan(dest_only=["local-only.md"]),
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert "Nothing to transfer" in _plain(capsys.readouterr().out)


@pytest.mark.asyncio
async def test_transfer_verbose_lists_each_file(config_home, tmp_path, capsys):
    root = tmp_path / "research"
    root.mkdir()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x")

    await webdav_project_transfer(
        "research",
        root,
        "pull",
        TransferPlan(new=["a.md"]),
        workspace_id="team-tenant",
        verbose=True,
        client_cm_factory=_client_factory(handler),
    )

    output = _plain(capsys.readouterr().out)
    assert "a.md" in output
    assert "Transferred 1 file(s)" in output


@pytest.mark.asyncio
async def test_transfer_stops_on_a_refused_upload(config_home, tmp_path):
    """A viewer pushing to a Team project fails loudly rather than half-succeeding."""
    root = tmp_path / "research"
    _write(root, "a.md", "content")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Editor access required")

    with pytest.raises(WebdavError, match="Editor access required"):
        await webdav_project_transfer(
            "research",
            root,
            "push",
            TransferPlan(new=["a.md"]),
            workspace_id="team-tenant",
            client_cm_factory=_client_factory(handler),
        )


# --- Diff over the wire ---


@pytest.mark.asyncio
async def test_webdav_project_diff_lists_the_cloud_and_compares(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "same.md", "identical")

    body = f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
<D:response><D:href>/webdav/research/</D:href><D:propstat><D:prop>
    <D:resourcetype><D:collection/></D:resourcetype><D:displayname>research</D:displayname>
</D:prop></D:propstat></D:response>
<D:response><D:href>/webdav/research/same.md</D:href><D:propstat><D:prop>
    <D:resourcetype/><D:displayname>same.md</D:displayname>
    <D:getcontentlength>9</D:getcontentlength>
    <D:getetag>"{_md5(b"identical")}"</D:getetag>
    <D:getlastmodified>Mon, 08 Jun 2026 10:30:00 GMT</D:getlastmodified>
</D:prop></D:propstat></D:response>
<D:response><D:href>/webdav/research/theirs.md</D:href><D:propstat><D:prop>
    <D:resourcetype/><D:displayname>theirs.md</D:displayname>
    <D:getcontentlength>6</D:getcontentlength>
    <D:getetag>"{_md5(b"theirs")}"</D:getetag>
    <D:getlastmodified>Mon, 08 Jun 2026 10:30:00 GMT</D:getlastmodified>
</D:prop></D:propstat></D:response>
</D:multistatus>"""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PROPFIND"
        return httpx.Response(207, text=body)

    plan = await webdav_project_diff(
        "research",
        root,
        "pull",
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert plan.new == ["theirs.md"]
    assert plan.conflicts == []
    assert plan.dest_only == []
