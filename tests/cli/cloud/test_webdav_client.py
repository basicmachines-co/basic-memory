"""Tests for the WebDAV client used by Team push/pull (#1262).

PROPFIND responses are fixtures rather than live calls: the validators these
tests rely on (entity tag, last-modified) are part of the contract this client
codes against, and a live cloud is not the thing under test here.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest

from basic_memory.cli.commands.cloud import webdav as webdav_module
from basic_memory.cli.commands.cloud.webdav import (
    WebdavError,
    download_file,
    etag_content_hash,
    list_project_files,
    normalize_etag,
    upload_file,
    webdav_path,
)


def _multistatus(self_href: str, entries: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
<D:response>
    <D:href>{self_href}</D:href>
    <D:propstat>
        <D:prop>
            <D:resourcetype><D:collection/></D:resourcetype>
            <D:displayname>self</D:displayname>
        </D:prop>
        <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
</D:response>
{entries}
</D:multistatus>"""


def _file_entry(
    href: str,
    name: str,
    size: int,
    *,
    etag: str | None = '"d41d8cd98f00b204e9800998ecf8427e"',
    modified: str | None = "Mon, 08 Jun 2026 10:30:00 GMT",
) -> str:
    props = [
        "<D:resourcetype/>",
        f"<D:displayname>{name}</D:displayname>",
        f"<D:getcontentlength>{size}</D:getcontentlength>",
    ]
    if etag is not None:
        props.append(f"<D:getetag>{etag}</D:getetag>")
    if modified is not None:
        props.append(f"<D:getlastmodified>{modified}</D:getlastmodified>")
    joined = "".join(props)
    return f"""<D:response>
    <D:href>{href}</D:href>
    <D:propstat><D:prop>{joined}</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
</D:response>"""


def _dir_entry(href: str, name: str) -> str:
    return f"""<D:response>
    <D:href>{href}</D:href>
    <D:propstat>
        <D:prop>
            <D:resourcetype><D:collection/></D:resourcetype>
            <D:displayname>{name}</D:displayname>
        </D:prop>
        <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
</D:response>"""


@asynccontextmanager
async def _client(handler):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://cloud.example.test"
    ) as client:
        yield client


def test_webdav_path_addresses_projects_and_files():
    assert webdav_path("research") == "/webdav/research"
    assert webdav_path("research", "notes/a.md") == "/webdav/research/notes/a.md"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ('"abc"', "abc"),
        ("  abc  ", "abc"),
        ('W/"abc"', "W/abc"),
        ('""', None),
    ],
)
def test_normalize_etag(raw, expected):
    assert normalize_etag(raw) == expected


@pytest.mark.parametrize(
    ("etag", "expected"),
    [
        (None, None),
        ("D41D8CD98F00B204E9800998ECF8427E", "d41d8cd98f00b204e9800998ecf8427e"),
        # Multipart digest-of-digests: not a content hash.
        ("d41d8cd98f00b204e9800998ecf8427e-3", None),
        # Weak validator: promises only semantic equivalence.
        ("W/d41d8cd98f00b204e9800998ecf8427e", None),
        ("opaque-tag", None),
    ],
)
def test_etag_content_hash_only_accepts_single_part_digests(etag, expected):
    assert etag_content_hash(etag) == expected


@pytest.mark.asyncio
async def test_list_project_files_sends_one_recursive_propfind():
    """One `Depth: infinity` request lists the whole project (#1604)."""
    listing = _multistatus(
        "/webdav/research/",
        _dir_entry("/webdav/research/notes/", "notes")
        + _dir_entry("/webdav/research/notes/deep/", "deep")
        + _file_entry("/webdav/research/notes/deep/deeper.md", "deeper.md", 2)
        + _file_entry("/webdav/research/notes/deep.md", "deep.md", 9)
        + _file_entry("/webdav/research/top.md", "top.md", 4),
    )
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(207, text=listing)

    async with _client(handler) as client:
        files = await list_project_files(client, "research")

    assert [(request.method, request.url.path) for request in seen] == [
        ("PROPFIND", "/webdav/research")
    ]
    assert seen[0].headers["Depth"] == "infinity"
    # Nested paths come from the hrefs; collections are not files.
    assert [f.path for f in files] == ["notes/deep/deeper.md", "notes/deep.md", "top.md"]

    top = files[2]
    assert top.size == 4
    assert top.etag == "d41d8cd98f00b204e9800998ecf8427e"
    assert top.modified == datetime(2026, 6, 8, 10, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_list_project_files_keeps_a_directory_named_like_its_parent():
    """`notes/notes/a.md` is a real nested file, not a repeat of `notes/a.md`."""
    listing = _multistatus(
        "/webdav/research/",
        _dir_entry("/webdav/research/notes/", "notes")
        + _dir_entry("/webdav/research/notes/notes/", "notes")
        + _file_entry("/webdav/research/notes/a.md", "a.md", 1)
        + _file_entry("/webdav/research/notes/notes/a.md", "a.md", 1),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=listing)

    async with _client(handler) as client:
        files = await list_project_files(client, "research")

    assert [f.path for f in files] == ["notes/a.md", "notes/notes/a.md"]


@pytest.mark.asyncio
async def test_list_project_files_decodes_percent_encoded_nested_hrefs():
    listing = _multistatus(
        "/webdav/My%20Research/",
        _dir_entry("/webdav/My%20Research/drafts%20%26%20ideas/", "drafts &amp; ideas")
        + _file_entry(
            "/webdav/My%20Research/drafts%20%26%20ideas/100%25%20done.md", "100% done.md", 3
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=listing)

    async with _client(handler) as client:
        files = await list_project_files(client, "My Research")

    assert [f.path for f in files] == ["drafts & ideas/100% done.md"]


@pytest.mark.asyncio
async def test_list_project_files_refuses_a_single_level_answer():
    """A service that ignores `Depth: infinity` must not be read as a full listing.

    Its answer names subdirectories with nothing inside them. Planning from it
    would treat every nested cloud file as absent, so the client stops instead of
    walking the directories itself.
    """
    listing = _multistatus(
        "/webdav/research/",
        _file_entry("/webdav/research/top.md", "top.md", 4)
        + _dir_entry("/webdav/research/notes/", "notes"),
    )
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(207, text=listing)

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="does not yet support the recursive listing"):
            await list_project_files(client, "research")

    assert seen == ["/webdav/research"]


@pytest.mark.asyncio
async def test_list_project_files_reports_a_refused_recursive_listing():
    """The RFC 4918 finite-depth refusal names the cause rather than a bare 403."""
    refusal = """<?xml version="1.0" encoding="utf-8"?>
<D:error xmlns:D="DAV:"><D:propfind-finite-depth/></D:error>"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=refusal)

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="more files than one listing may describe"):
            await list_project_files(client, "research")


@pytest.mark.asyncio
async def test_list_project_files_falls_back_to_the_href_for_a_name():
    """A server that omits displayname is still usable: the href carries the name."""
    body = _multistatus(
        "/webdav/research/",
        """<D:response>
    <D:href>/webdav/research/with%20space.md</D:href>
    <D:propstat><D:prop><D:resourcetype/>
        <D:getcontentlength>7</D:getcontentlength>
    </D:prop></D:propstat>
</D:response>""",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    async with _client(handler) as client:
        files = await list_project_files(client, "research")

    assert [f.path for f in files] == ["with space.md"]
    # No validators offered at all — the caller must decide, not assume.
    assert files[0].etag is None
    assert files[0].modified is None


@pytest.mark.asyncio
async def test_list_project_files_reports_an_entry_with_no_href():
    body = _multistatus(
        "/webdav/research/",
        "<D:response><D:propstat><D:prop><D:resourcetype/></D:prop></D:propstat></D:response>",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="no href"):
            await list_project_files(client, "research")


@pytest.mark.parametrize(
    ("href", "match"),
    [
        ("/webdav/elsewhere/a.md", "outside it"),
        ("/webdav/research/", "repeats the collection"),
    ],
)
@pytest.mark.asyncio
async def test_list_project_files_rejects_an_href_that_is_not_a_descendant(href, match):
    body = _multistatus("/webdav/research/", _file_entry(href, "a.md", 1))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match=match):
            await list_project_files(client, "research")


@pytest.mark.asyncio
async def test_list_project_files_rejects_a_display_name_the_href_contradicts():
    """An href that decodes to a different name cannot be trusted to address the file."""
    body = _multistatus(
        "/webdav/research/",
        _file_entry("/webdav/research/100%25done.md", "100%25done.md", 1),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="at an href that decodes to"):
            await list_project_files(client, "research")


@pytest.mark.asyncio
async def test_list_project_files_reports_unparseable_xml():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text="<D:multistatus")

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="Could not parse"):
            await list_project_files(client, "research")


@pytest.mark.asyncio
async def test_list_project_files_reports_a_rejected_listing():
    """A viewer-less caller gets a clear failure, not an empty project."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="HTTP 403"):
            await list_project_files(client, "research")


@pytest.mark.asyncio
async def test_list_project_files_reports_a_non_numeric_size():
    body = _multistatus(
        "/webdav/research/",
        """<D:response>
    <D:href>/webdav/research/a.md</D:href>
    <D:propstat><D:prop><D:resourcetype/><D:displayname>a.md</D:displayname>
        <D:getcontentlength>huge</D:getcontentlength>
    </D:prop></D:propstat>
</D:response>""",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="non-numeric file size"):
            await list_project_files(client, "research")


@pytest.mark.asyncio
async def test_list_project_files_reports_an_unparseable_timestamp():
    body = _multistatus(
        "/webdav/research/",
        _file_entry("/webdav/research/a.md", "a.md", 1, modified="yesterday-ish"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="unparseable timestamp"):
            await list_project_files(client, "research")


@pytest.mark.asyncio
async def test_list_project_files_ignores_a_propstat_without_props():
    body = _multistatus(
        "/webdav/research/",
        """<D:response>
    <D:href>/webdav/research/a.md</D:href>
    <D:propstat><D:status>HTTP/1.1 404 Not Found</D:status></D:propstat>
    <D:propstat><D:prop><D:resourcetype/><D:displayname>a.md</D:displayname>
        <D:getcontentlength>3</D:getcontentlength>
    </D:prop></D:propstat>
</D:response>""",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    async with _client(handler) as client:
        files = await list_project_files(client, "research")

    assert [(f.path, f.size) for f in files] == [("a.md", 3)]


@pytest.mark.asyncio
async def test_download_file_returns_content_and_last_modified():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/webdav/research/notes/a.md"
        return httpx.Response(
            200,
            content=b"hello",
            headers={"Last-Modified": "Mon, 08 Jun 2026 10:30:00 GMT"},
        )

    async with _client(handler) as client:
        downloaded = await download_file(client, "research", "notes/a.md")

    assert downloaded.content == b"hello"
    assert downloaded.modified == datetime(2026, 6, 8, 10, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_download_file_reports_a_refused_download():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="File not found")

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="HTTP 404"):
            await download_file(client, "research", "gone.md")


@pytest.mark.asyncio
async def test_upload_file_puts_content_with_the_local_mtime():
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["mtime"] = request.headers["X-OC-Mtime"]
        seen["content"] = request.content
        return httpx.Response(201)

    async with _client(handler) as client:
        await upload_file(client, "research", "notes/a.md", content=b"hi", mtime=1780000000)

    assert seen == {
        "method": "PUT",
        "path": "/webdav/research/notes/a.md",
        "mtime": "1780000000",
        "content": b"hi",
    }


@pytest.mark.asyncio
async def test_upload_file_reports_a_refused_upload():
    """A viewer pushing to a Team project gets the service's refusal verbatim."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Editor access required")

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="Editor access required"):
            await upload_file(client, "research", "a.md", content=b"hi", mtime=1)


@pytest.mark.asyncio
async def test_transport_errors_are_reported_without_a_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="connection refused"):
            await download_file(client, "research", "a.md")


@pytest.mark.parametrize(
    ("project", "rel_path", "expected"),
    [
        # `#` and `?` are URL delimiters, so leaving them raw would truncate the
        # request path at a perfectly legal filename character.
        ("research", "a#draft.md", "/webdav/research/a%23draft.md"),
        ("research", "b?v2.md", "/webdav/research/b%3Fv2.md"),
        ("research", "notes/c d.md", "/webdav/research/notes/c%20d.md"),
        ("my project", "a.md", "/webdav/my%20project/a.md"),
        # Separators stay separators.
        ("research", "one/two/three.md", "/webdav/research/one/two/three.md"),
    ],
)
def test_webdav_path_percent_encodes_without_losing_separators(project, rel_path, expected):
    assert webdav_path(project, rel_path) == expected


@pytest.mark.asyncio
async def test_delimiter_filenames_round_trip_through_list_and_download():
    """A note named `a#draft.md` must be listed, then fetched, as that same file."""
    listing = _multistatus(
        "/webdav/research/",
        _file_entry("/webdav/research/a%23draft.md", "a#draft.md", 4),
    )
    fetched: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=listing)
        fetched.append(request.url.path)
        return httpx.Response(200, content=b"body")

    async with _client(handler) as client:
        files = await list_project_files(client, "research")
        await download_file(client, "research", files[0].path)

    assert [f.path for f in files] == ["a#draft.md"]
    # httpx reports the decoded path; the delimiter survived the round trip.
    assert fetched == ["/webdav/research/a#draft.md"]


@pytest.mark.asyncio
async def test_upload_file_create_only_sends_the_conditional_header():
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["if_none_match"] = request.headers.get("If-None-Match")
        return httpx.Response(201)

    async with _client(handler) as client:
        written = await upload_file(
            client, "research", "a.md", content=b"hi", mtime=1, create_only=True
        )

    assert written is True
    assert seen == {"if_none_match": "*"}


@pytest.mark.asyncio
async def test_upload_file_create_only_reports_a_refused_precondition():
    """412 is the answer the request asked for, not a failure to raise on."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, text="Precondition Failed")

    async with _client(handler) as client:
        written = await upload_file(
            client, "research", "a.md", content=b"hi", mtime=1, create_only=True
        )

    assert written is False


@pytest.mark.asyncio
async def test_upload_file_without_create_only_still_raises_on_412():
    """Only a conditional write can interpret 412; anywhere else it is an error."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, text="Precondition Failed")

    async with _client(handler) as client:
        with pytest.raises(WebdavError, match="HTTP 412"):
            await upload_file(client, "research", "a.md", content=b"hi", mtime=1)


# --- Rate-limit retry (#2039) ---


@pytest.fixture
def recorded_waits(monkeypatch):
    """Collect the waits this client would take instead of spending them."""
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(webdav_module, "_sleep", fake_sleep)
    return waits


def _rate_limited(retry_after: str | None = "4") -> httpx.Response:
    headers = {
        "X-RateLimit-Limit": "120",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "1789045631",
    }
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return httpx.Response(
        429,
        headers=headers,
        json={
            "detail": {
                "code": "rate_limit_exceeded",
                "message": "Too many requests. Retry after the current rate-limit window resets.",
                "scope": "principal",
            }
        },
    )


@pytest.mark.asyncio
async def test_propfind_listing_survives_a_rate_limit(recorded_waits):
    """A 429 on the listing must not abort the transfer.

    This is the reported failure: one throttled PROPFIND aborted the whole
    project walk, and because each re-run restarted at the first directory the
    pull could never finish.
    """
    attempts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) == 1:
            return _rate_limited()
        return httpx.Response(
            200,
            text=_multistatus(
                "/webdav/research/",
                _file_entry("/webdav/research/a.md", "a.md", 3),
            ),
        )

    async with _client(handler) as client:
        files = await list_project_files(client, "research")

    assert [file.path for file in files] == ["a.md"]
    assert len(attempts) == 2
    assert recorded_waits == [4.0]


@pytest.mark.asyncio
async def test_download_survives_a_rate_limit(recorded_waits):
    """The transfer phase is metered too, so GET needs the same handling."""
    attempts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.method)
        if len(attempts) == 1:
            return _rate_limited()
        return httpx.Response(200, content=b"body")

    async with _client(handler) as client:
        downloaded = await download_file(client, "research", "a.md")

    assert downloaded.content == b"body"
    assert recorded_waits == [4.0]


@pytest.mark.asyncio
async def test_upload_retry_replays_the_same_write(recorded_waits):
    """A replayed PUT must be the identical request, not a second effect."""
    seen: list[tuple[bytes, str | None, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.content,
                request.headers.get("X-OC-Mtime"),
                request.headers.get("If-None-Match"),
            )
        )
        if len(seen) == 1:
            return _rate_limited()
        return httpx.Response(201)

    async with _client(handler) as client:
        written = await upload_file(
            client, "research", "a.md", content=b"body", mtime=1789045631, create_only=True
        )

    assert written is True
    assert seen[0] == seen[1] == (b"body", "1789045631", "*")
    assert recorded_waits == [4.0]


@pytest.mark.asyncio
async def test_create_only_precondition_is_not_retried(recorded_waits):
    """412 is this call's answer, not a rejection to wait out."""
    attempts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.method)
        return httpx.Response(412)

    async with _client(handler) as client:
        written = await upload_file(
            client, "research", "a.md", content=b"body", mtime=1, create_only=True
        )

    assert written is False
    assert len(attempts) == 1
    assert recorded_waits == []


@pytest.mark.asyncio
async def test_persistent_rate_limit_reports_the_headers(recorded_waits):
    """A transfer that cannot get through must say what stopped it."""
    attempts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.method)
        return _rate_limited()

    async with _client(handler) as client:
        with pytest.raises(WebdavError) as caught:
            await list_project_files(client, "research")

    message = str(caught.value)
    assert "HTTP 429" in message
    assert "rate limit: limit 120, remaining 0, retry after 4, resets at 1789045631" in message
    # Bounded: the attempts stop, and only the waits between them are taken.
    assert len(attempts) == 6
    assert recorded_waits == [4.0] * 5


@pytest.mark.asyncio
async def test_non_rate_limit_errors_are_not_retried(recorded_waits):
    """Only 429 is a wait-and-repeat answer; a 404 is final."""
    attempts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.method)
        return httpx.Response(404, text="missing")

    async with _client(handler) as client:
        with pytest.raises(WebdavError) as caught:
            await download_file(client, "research", "a.md")

    assert len(attempts) == 1
    assert recorded_waits == []
    # No rate-limit detail is appended to a status that has none.
    assert "rate limit" not in str(caught.value)


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("4", 4.0),
        ("0", 1.0),  # floored, so "retry immediately" cannot spin
        ("9999", 60.0),  # capped, so an implausible header cannot hang the CLI
        (None, 5.0),  # absent: the window is still closed
        ("not-a-date", 5.0),  # unparseable falls back rather than giving up
        ("Mon, 08 Jun 2026 10:30:00 GMT", 1.0),  # a past HTTP-date floors
    ],
)
def test_retry_after_seconds(retry_after, expected):
    response = _rate_limited(retry_after=retry_after)
    assert webdav_module._retry_after_seconds(response) == expected
