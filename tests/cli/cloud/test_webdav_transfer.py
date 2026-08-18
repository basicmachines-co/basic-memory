"""Tests for the WebDAV push/pull engine used on Team workspaces (#1262).

These exercise the real comparison and transfer code against a mocked WebDAV
surface, so every `--on-conflict` strategy is proven to behave the way it does on
the Personal (rclone) path.
"""

import errno
import hashlib
import importlib
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


def _propfind_body(paths: list[str]) -> str:
    """A minimal project listing naming exactly `paths` as files."""
    entries = "".join(
        f"""<D:response><D:href>/webdav/research/{path}</D:href><D:propstat><D:prop>
    <D:resourcetype/><D:displayname>{path.split("/")[-1]}</D:displayname>
    <D:getcontentlength>1</D:getcontentlength>
</D:prop></D:propstat></D:response>"""
        for path in paths
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
<D:response><D:href>/webdav/research/</D:href><D:propstat><D:prop>
    <D:resourcetype><D:collection/></D:resourcetype><D:displayname>research</D:displayname>
</D:prop></D:propstat></D:response>
{entries}
</D:multistatus>"""


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
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body([]))
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
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body([]))
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
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body([]))
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
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body([]))
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


# --- Symlinks never let a transfer leave the project boundary ---


def test_scan_skips_symlinked_files(config_home, tmp_path):
    """Push must not read bytes from outside the project through a link."""
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    root = tmp_path / "research"
    _write(root, "real.md", "real")
    (root / "link.md").symlink_to(outside)

    patterns = load_gitignore_patterns(root, use_gitignore=False)
    assert sorted(scan_local_files(root, patterns)) == ["real.md"]


def test_scan_does_not_descend_into_symlinked_directories(config_home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    root = tmp_path / "research"
    _write(root, "real.md", "real")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    patterns = load_gitignore_patterns(root, use_gitignore=False)
    assert sorted(scan_local_files(root, patterns)) == ["real.md"]


@pytest.mark.asyncio
async def test_pull_refuses_to_write_through_a_symlinked_directory(config_home, tmp_path):
    """A lexically clean path can still point outside once a link is resolved."""
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "research"
    root.mkdir()
    (root / "notes").symlink_to(outside, target_is_directory=True)

    async def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must refuse before touching the network")

    with pytest.raises(WebdavError, match="link out of the project"):
        await webdav_project_transfer(
            "research",
            root,
            "pull",
            TransferPlan(new=["notes/planted.md"]),
            workspace_id="team-tenant",
            client_cm_factory=_client_factory(handler),
        )

    assert not (outside / "planted.md").exists()


@pytest.mark.asyncio
async def test_push_refuses_to_read_through_a_symlinked_directory(config_home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    root = tmp_path / "research"
    root.mkdir()
    (root / "notes").symlink_to(outside, target_is_directory=True)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body([]))
        raise AssertionError("must refuse before uploading anything")

    with pytest.raises(WebdavError, match="link out of the project"):
        await webdav_project_transfer(
            "research",
            root,
            "push",
            TransferPlan(new=["notes/secret.md"]),
            workspace_id="team-tenant",
            client_cm_factory=_client_factory(handler),
        )


@pytest.mark.asyncio
async def test_pull_keep_cloud_refuses_to_overwrite_a_symlinked_note(config_home, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("original", encoding="utf-8")
    root = tmp_path / "research"
    root.mkdir()
    (root / "dup.md").symlink_to(outside)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"cloud version")

    with pytest.raises(WebdavError, match="symlinked path"):
        await webdav_project_transfer(
            "research",
            root,
            "pull",
            TransferPlan(conflicts=["dup.md"]),
            workspace_id="team-tenant",
            strategy="keep-cloud",
            client_cm_factory=_client_factory(handler),
        )

    assert outside.read_text() == "original"


# --- A path planned as new is created, never used to replace ---


@pytest.mark.asyncio
async def test_pull_leaves_a_local_note_that_appeared_after_planning(config_home, tmp_path, capsys):
    """The plan is a snapshot; the exclusive create is what makes acting on it safe."""
    root = tmp_path / "research"
    root.mkdir()
    # Written after the plan classified this path as new — a note nobody compared.
    _write(root, "raced.md", "written since the plan")

    async def handler(request: httpx.Request) -> httpx.Response:
        # The download does run — nothing may be staged at the destination until
        # the bytes exist — but publication is what refuses.
        return httpx.Response(200, content=b"from cloud")

    await webdav_project_transfer(
        "research",
        root,
        "pull",
        TransferPlan(new=["raced.md"]),
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert (root / "raced.md").read_text() == "written since the plan"
    # The staged temp file is cleaned up either way.
    assert sorted(p.name for p in root.iterdir()) == ["raced.md"]
    output = _plain(capsys.readouterr().out)
    assert "Transferred 0 file(s)" in output
    assert "appeared on the destination" in output
    assert "raced.md" in output


@pytest.mark.asyncio
async def test_push_leaves_a_cloud_note_that_appeared_after_planning(config_home, tmp_path, capsys):
    """Mirrors rclone's --ignore-existing, which re-reads the destination at copy time."""
    root = tmp_path / "research"
    _write(root, "raced.md", "local content")
    _write(root, "fresh.md", "also local")

    listing = _propfind_body(["raced.md"])
    puts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=listing)
        puts.append(request.url.path)
        return httpx.Response(201)

    await webdav_project_transfer(
        "research",
        root,
        "push",
        TransferPlan(new=["fresh.md", "raced.md"]),
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert puts == ["/webdav/research/fresh.md"]
    output = _plain(capsys.readouterr().out)
    assert "Transferred 1 file(s)" in output
    assert "appeared on the destination" in output
    assert "raced.md" in output


@pytest.mark.asyncio
async def test_push_keep_local_still_overwrites_a_resolved_conflict(config_home, tmp_path):
    """An explicit resolution is an instruction to overwrite, not a stale guess."""
    root = tmp_path / "research"
    _write(root, "dup.md", "local wins")

    puts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body(["dup.md"]))
        puts.append(request.url.path)
        return httpx.Response(204)

    await webdav_project_transfer(
        "research",
        root,
        "push",
        TransferPlan(conflicts=["dup.md"]),
        workspace_id="team-tenant",
        strategy="keep-local",
        client_cm_factory=_client_factory(handler),
    )

    assert puts == ["/webdav/research/dup.md"]


@pytest.mark.asyncio
async def test_pull_keep_cloud_still_overwrites_a_resolved_conflict(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "dup.md", "local version")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"cloud version")

    await webdav_project_transfer(
        "research",
        root,
        "pull",
        TransferPlan(conflicts=["dup.md"]),
        workspace_id="team-tenant",
        strategy="keep-cloud",
        client_cm_factory=_client_factory(handler),
    )

    assert (root / "dup.md").read_text() == "cloud version"


@pytest.mark.asyncio
async def test_pull_leaves_no_placeholder_when_the_download_fails(config_home, tmp_path):
    """The exclusive create must not survive as an empty phantom note."""
    root = tmp_path / "research"
    root.mkdir()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(WebdavError, match="HTTP 500"):
        await webdav_project_transfer(
            "research",
            root,
            "pull",
            TransferPlan(new=["a.md"]),
            workspace_id="team-tenant",
            client_cm_factory=_client_factory(handler),
        )

    assert not (root / "a.md").exists()


# --- Filenames that are legal on disk but structural in a URL ---


@pytest.mark.asyncio
async def test_pull_round_trips_filenames_containing_url_delimiters(config_home, tmp_path):
    """`#` and `?` are path data here, not a fragment and a query."""
    root = tmp_path / "research"
    root.mkdir()
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        # httpx decodes the path it received; the delimiters must survive intact.
        requested.append(request.url.path)
        return httpx.Response(200, content=b"body")

    # `?` is exercised in the client tests instead: Windows will not allow a file
    # by that name on disk, and this test has to actually write one.
    plan = TransferPlan(new=["notes/a#draft.md", "notes/c d.md"])
    await webdav_project_transfer(
        "research",
        root,
        "pull",
        plan,
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert requested == [
        "/webdav/research/notes/a#draft.md",
        "/webdav/research/notes/c d.md",
    ]
    assert (root / "notes" / "a#draft.md").read_bytes() == b"body"
    assert (root / "notes" / "c d.md").read_bytes() == b"body"


@pytest.mark.asyncio
async def test_push_sends_filenames_containing_url_delimiters_intact(config_home, tmp_path):
    root = tmp_path / "research"
    _write(root, "a#draft.md", "content")
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body([]))
        requested.append(request.url.path)
        return httpx.Response(201)

    await webdav_project_transfer(
        "research",
        root,
        "push",
        TransferPlan(new=["a#draft.md"]),
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert requested == ["/webdav/research/a#draft.md"]


# --- Nothing is created at the destination before the bytes exist ---


@pytest.mark.asyncio
async def test_pull_leaves_a_note_created_during_the_download(config_home, tmp_path, capsys):
    """The destination is claimed after the download, so a note that lands mid-flight survives."""
    root = tmp_path / "research"
    root.mkdir()

    async def handler(request: httpx.Request) -> httpx.Response:
        # Racing writer: the note appears while the download is in flight.
        _write(root, "raced.md", "written during the download")
        return httpx.Response(200, content=b"from cloud")

    await webdav_project_transfer(
        "research",
        root,
        "pull",
        TransferPlan(new=["raced.md"]),
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert (root / "raced.md").read_text() == "written during the download"
    output = _plain(capsys.readouterr().out)
    assert "Transferred 0 file(s)" in output
    assert "appeared on the destination" in output


@pytest.mark.asyncio
async def test_pull_leaves_content_written_into_the_destination_during_the_download(
    config_home, tmp_path
):
    """No empty placeholder exists for an editor to fill and have discarded."""
    root = tmp_path / "research"
    root.mkdir()
    observed: list[list[str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        # An editor opening the destination mid-download must find nothing there
        # to open, and its content must survive whatever the transfer does next.
        observed.append(sorted(p.name for p in root.iterdir()))
        _write(root, "raced.md", "editor content")
        return httpx.Response(200, content=b"from cloud")

    await webdav_project_transfer(
        "research",
        root,
        "pull",
        TransferPlan(new=["raced.md"]),
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert observed == [[]]  # nothing staged at the destination before the bytes existed
    assert (root / "raced.md").read_text() == "editor content"


@pytest.mark.asyncio
async def test_pull_leaves_nothing_behind_when_publication_fails(
    config_home, tmp_path, monkeypatch
):
    """A failure after a successful download must not leave a phantom note."""
    root = tmp_path / "research"
    root.mkdir()

    module = importlib.import_module("basic_memory.cli.commands.cloud.webdav_transfer")

    def _explode(*_args, **_kwargs):
        raise OSError("publication failed")

    monkeypatch.setattr(module.os, "link", _explode)
    monkeypatch.setattr(module.os, "open", _explode)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"from cloud")

    with pytest.raises(OSError, match="publication failed"):
        await webdav_project_transfer(
            "research",
            root,
            "pull",
            TransferPlan(new=["a.md"]),
            workspace_id="team-tenant",
            client_cm_factory=_client_factory(handler),
        )

    assert sorted(p.name for p in root.iterdir()) == []


@pytest.mark.asyncio
async def test_pull_publishes_without_hardlinks_when_the_filesystem_cannot(
    config_home, tmp_path, monkeypatch
):
    """exFAT and some virtual mounts have no hardlinks; the no-clobber rule still holds."""
    root = tmp_path / "research"
    root.mkdir()
    _write(root, "taken.md", "already here")

    module = importlib.import_module("basic_memory.cli.commands.cloud.webdav_transfer")

    def _unsupported(*_args, **_kwargs):
        raise OSError(errno.EPERM, "hardlinks are not supported here")

    monkeypatch.setattr(module.os, "link", _unsupported)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"from cloud",
            headers={"Last-Modified": "Mon, 08 Jun 2026 10:30:00 GMT"},
        )

    await webdav_project_transfer(
        "research",
        root,
        "pull",
        TransferPlan(new=["fresh.md", "taken.md"]),
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    # The new name is written, timestamp and all; the taken one is untouched.
    assert (root / "fresh.md").read_bytes() == b"from cloud"
    assert (root / "fresh.md").stat().st_mtime == pytest.approx(MODIFIED.timestamp())
    assert (root / "taken.md").read_text() == "already here"


# --- The conditional create, not the re-list, is what holds the line on push ---


@pytest.mark.asyncio
async def test_push_sends_a_conditional_create_and_honors_a_refusal(config_home, tmp_path, capsys):
    """A path created after the re-list is refused at the write itself."""
    root = tmp_path / "research"
    _write(root, "raced.md", "local content")
    _write(root, "fresh.md", "also local")

    seen: list[tuple[str, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            # The listing is clean: this race opens *after* it.
            return httpx.Response(207, text=_propfind_body([]))
        seen.append((request.url.path, request.headers.get("If-None-Match")))
        if request.url.path.endswith("raced.md"):
            return httpx.Response(412, text="Precondition Failed")
        return httpx.Response(201)

    await webdav_project_transfer(
        "research",
        root,
        "push",
        TransferPlan(new=["fresh.md", "raced.md"]),
        workspace_id="team-tenant",
        client_cm_factory=_client_factory(handler),
    )

    assert seen == [
        ("/webdav/research/fresh.md", "*"),
        ("/webdav/research/raced.md", "*"),
    ]
    output = _plain(capsys.readouterr().out)
    assert "Transferred 1 file(s)" in output
    assert "appeared on the destination" in output
    assert "raced.md" in output


@pytest.mark.asyncio
async def test_push_keep_local_does_not_send_a_conditional_create(config_home, tmp_path):
    """An explicit resolution is an instruction to replace, so it must not be refused."""
    root = tmp_path / "research"
    _write(root, "dup.md", "local wins")

    seen: list[tuple[str, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body(["dup.md"]))
        seen.append((request.url.path, request.headers.get("If-None-Match")))
        return httpx.Response(204)

    await webdav_project_transfer(
        "research",
        root,
        "push",
        TransferPlan(conflicts=["dup.md"]),
        workspace_id="team-tenant",
        strategy="keep-local",
        client_cm_factory=_client_factory(handler),
    )

    assert seen == [("/webdav/research/dup.md", None)]


@pytest.mark.asyncio
async def test_push_keep_both_conflict_copies_are_conditional(config_home, tmp_path):
    """A conflict copy is a create too — it must never land on an existing name."""
    root = tmp_path / "research"
    _write(root, "dup.md", "local version")

    seen: list[tuple[str, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_propfind_body([]))
        seen.append((request.url.path, request.headers.get("If-None-Match")))
        return httpx.Response(201)

    await webdav_project_transfer(
        "research",
        root,
        "push",
        TransferPlan(conflicts=["dup.md"]),
        workspace_id="team-tenant",
        strategy="keep-both",
        conflict_suffix="S",
        client_cm_factory=_client_factory(handler),
    )

    assert seen == [("/webdav/research/dup.conflict-S.md", "*")]
