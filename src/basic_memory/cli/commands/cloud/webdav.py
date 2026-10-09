"""Client for the cloud WebDAV file surface.

`bm cloud upload` already speaks the write half of this protocol: a plain
``PUT /webdav/{project}/{path}`` carrying an ``X-OC-Mtime`` header. Team
`push`/`pull` need the read half as well (#1262) — ``PROPFIND`` to enumerate a
project and ``GET`` to fetch a file — together with the validators (entity tag,
last-modified) that let a transfer decide whether two sides actually differ.

This module sits beside ``upload.py`` rather than inside it. ``upload.py`` is the
implementation of one command: a directory walk that prints its own progress and
owns that command's filtering rules. What follows is protocol only — no CLI
output, no policy — so that the push/pull engine in ``webdav_transfer.py`` and
the upload command can share one definition of how a project's files are
addressed.

Access control is the reason this transport exists at all. Every request here is
authorized by the service against the caller's access to *this* project, whereas
object-storage credentials are scoped to an entire tenant bucket and cannot
express per-project access.
"""

import asyncio
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import httpx

WEBDAV_ROOT = "/webdav"

DAV_NS = "{DAV:}"

# The standard PROPFIND body. The properties we need (entity tag, last-modified,
# content length, resource type, display name) are all live DAV properties, so
# `allprop` asks for exactly the right set without enumerating them.
_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>\n<D:propfind xmlns:D="DAV:"><D:allprop/></D:propfind>'
)

# An object store reports a single-part object's entity tag as the MD5 digest of
# its bytes: 32 hex characters. Anything else — an opaque tag, a weak validator,
# or a multipart digest-of-digests with its "-N" part-count suffix — is not a
# content hash and must never be compared as one.
_CONTENT_HASH_PATTERN = re.compile(r"[0-9a-fA-F]{32}")

# The service meters every request on this transport, and a transfer of any real
# size will meet its own rate limit: the transfer that follows the project
# listing costs one request per file. A 429 is
# therefore an ordinary step in a healthy transfer rather than a failure, and the
# response carries a Retry-After telling us when the window resets.
#
# Retrying is what makes progress possible at all. Before this, a single 429
# aborted the whole transfer, and since every re-run started over, a project
# past the per-minute ceiling could never finish no matter how long the user
# waited (#2039).
_RATE_LIMIT_MAX_ATTEMPTS = 6
# A single wait is capped so an implausible Retry-After cannot hang the CLI, and
# floored at one second so a "retry immediately" answer cannot spin.
_RATE_LIMIT_MAX_WAIT_SECONDS = 60.0
_RATE_LIMIT_MIN_WAIT_SECONDS = 1.0
# Used when a 429 arrives with no Retry-After, or one we cannot parse.
_RATE_LIMIT_FALLBACK_WAIT_SECONDS = 5.0


class WebdavError(Exception):
    """Raised when the cloud WebDAV surface cannot be read or written."""


@dataclass(frozen=True)
class RemoteFile:
    """One file as the cloud reports it in a PROPFIND listing.

    ``etag`` and ``modified`` are optional because a server is free to omit
    either. Callers must decide what to do without them rather than assume.
    """

    path: str  # project-relative POSIX path
    size: int
    etag: str | None
    modified: datetime | None


@dataclass(frozen=True)
class DownloadedFile:
    """A file fetched over ``GET``, with whatever validators came back with it."""

    content: bytes
    modified: datetime | None


@dataclass(frozen=True)
class _Entry:
    """One ``<D:response>`` element, before collections and files are separated."""

    rel_path: str
    is_collection: bool
    size: int
    etag: str | None
    modified: datetime | None


def webdav_path(project: str, rel_path: str = "") -> str:
    """Build the request path for a project, or for a file inside it.

    Callers pass the name and path unescaped; this percent-encodes them, keeping
    ``/`` as the separator. Leaving that to the HTTP client is not enough: ``?``
    and ``#`` are structural URL delimiters, not path data, so a note named
    ``a#draft.md`` would be requested as ``a`` — a 404, or worse, another
    object's bytes. Both are legal POSIX filenames.
    """
    encoded_project = quote(project, safe="")
    if not rel_path:
        return f"{WEBDAV_ROOT}/{encoded_project}"
    return f"{WEBDAV_ROOT}/{encoded_project}/{quote(rel_path, safe='/')}"


def normalize_etag(raw: str | None) -> str | None:
    """Strip the surrounding quotes from an entity tag, keeping any weak marker.

    The ``W/`` prefix is deliberately preserved: a weak validator promises only
    semantic equivalence, never byte equality, so ``etag_content_hash`` has to be
    able to see it and refuse.
    """
    if raw is None:
        return None
    value = raw.strip()
    if value.startswith("W/"):
        inner = value[2:].strip().strip('"')
        return f"W/{inner}"
    return value.strip('"') or None


def etag_content_hash(etag: str | None) -> str | None:
    """Return the entity tag as a usable content hash, or None when it is not one.

    "Not one" covers a missing tag, an opaque or weak tag, and the multipart
    ``<hex>-<N>`` shape — for a multipart upload the store hashes the part
    digests, so the same bytes stored differently produce a different value.
    Callers must fall back to another comparison rather than treating an
    unusable tag as either a match or a conflict (#1262).
    """
    if etag is None:
        return None
    if not _CONTENT_HASH_PATTERN.fullmatch(etag):
        return None
    return etag.lower()


# --- Rate-limit retry ---


async def _sleep(seconds: float) -> None:
    """Wait out a rate-limit window.

    A module-level seam so tests can observe the waits this client would take
    without spending them.
    """
    await asyncio.sleep(seconds)


def _retry_after_seconds(response: httpx.Response) -> float:
    """Read Retry-After as a wait in seconds, clamped to a sane range.

    RFC 9110 allows either delay-seconds or an HTTP-date, and the value is
    advisory: a header we cannot parse still tells us the window is closed, so
    every unusable form falls back to a fixed wait rather than giving up.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return _RATE_LIMIT_FALLBACK_WAIT_SECONDS

    candidate = raw.strip()
    if candidate.isdigit():
        seconds = float(candidate)
    else:
        try:
            retry_at = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, OverflowError):
            return _RATE_LIMIT_FALLBACK_WAIT_SECONDS
        if retry_at.tzinfo is None:
            return _RATE_LIMIT_FALLBACK_WAIT_SECONDS
        seconds = (retry_at - datetime.now(retry_at.tzinfo)).total_seconds()

    return min(max(seconds, _RATE_LIMIT_MIN_WAIT_SECONDS), _RATE_LIMIT_MAX_WAIT_SECONDS)


async def _request_with_rate_limit_retry(
    client: httpx.AsyncClient,
    method: str,
    request_path: str,
    *,
    content: bytes | str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Send one request, waiting out any rate-limit rejections.

    Every request this module sends is safe to repeat: the reads have no body,
    and the one write carries the same bytes and headers each time, so a replay
    is the identical request rather than a second effect. The body is passed
    explicitly rather than forwarded, so a caller cannot quietly add a parameter
    that makes a retry something other than the same request again.

    A 429 on the final attempt is returned rather than raised, so the caller's
    own error handling reports it with the rate-limit detail attached.
    """
    for remaining in range(_RATE_LIMIT_MAX_ATTEMPTS - 1, -1, -1):
        response = await client.request(method, request_path, content=content, headers=headers)
        if response.status_code != httpx.codes.TOO_MANY_REQUESTS or remaining == 0:
            return response
        await _sleep(_retry_after_seconds(response))

    raise AssertionError("unreachable: the loop returns on its final attempt")


async def list_project_files(client: httpx.AsyncClient, project: str) -> list[RemoteFile]:
    """Enumerate every file in a cloud project with one recursive PROPFIND.

    The service answers ``Depth: infinity`` with every file below the project in
    a single multistatus document. This replaced a walk of one ``Depth: 1``
    request per directory, which took most of a transfer's time (#1604).

    Raises:
        WebdavError: If the service rejects the listing, refuses a recursive
            listing, answers with a single-level one, or returns XML we cannot
            interpret.
    """
    request_path = webdav_path(project)
    try:
        response = await _request_with_rate_limit_retry(
            client,
            "PROPFIND",
            request_path,
            content=_PROPFIND_BODY,
            headers={"Depth": "infinity", "Content-Type": "application/xml"},
        )
        # Trigger: the service refused the recursive listing (RFC 4918 9.1).
        # Why: it does this for a project larger than one response may describe.
        # A per-directory walk would work around it, but silently falling back
        # would hide that the service has drawn a line here.
        # Outcome: stop with a message that names the cause.
        if response.status_code == httpx.codes.FORBIDDEN and _is_finite_depth_refusal(
            response.text
        ):
            raise WebdavError(
                f"The cloud refused to list project '{project}' in one request: it has "
                "more files than one listing may describe. Push and pull cannot run "
                "on this project yet."
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WebdavError(f"Failed to list cloud project '{project}': {_describe(exc)}") from exc

    entries = _parse_propfind(response.text, request_path=request_path)
    _require_recursive_listing(entries, project)
    return [
        RemoteFile(
            path=entry.rel_path,
            size=entry.size,
            etag=entry.etag,
            modified=entry.modified,
        )
        for entry in entries
        if not entry.is_collection
    ]


async def download_file(client: httpx.AsyncClient, project: str, rel_path: str) -> DownloadedFile:
    """Fetch one file, along with the last-modified time the service reports.

    Raises:
        WebdavError: If the service refuses the download.
    """
    request_path = webdav_path(project, rel_path)
    try:
        response = await _request_with_rate_limit_retry(client, "GET", request_path)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WebdavError(f"Failed to download {rel_path}: {_describe(exc)}") from exc

    return DownloadedFile(
        content=response.content,
        modified=_parse_http_date(response.headers.get("Last-Modified")),
    )


async def upload_file(
    client: httpx.AsyncClient,
    project: str,
    rel_path: str,
    *,
    content: bytes,
    mtime: int,
    create_only: bool = False,
) -> bool:
    """Write one file, advertising the local modification time.

    ``X-OC-Mtime`` (the ownCloud/Nextcloud convention) is what `bm cloud upload`
    already sends, so the two write paths look identical to the service.

    ``create_only`` sends ``If-None-Match: *``, which asks the service to refuse
    the write if the resource already exists. That precondition is evaluated at
    the moment of the write, which is the only place a client-side check cannot
    reach: any listing this client did beforehand is already stale by the time
    the request lands.

    Returns:
        True when the file was written; False when a create-only write was
        refused because the resource already exists.

    Raises:
        WebdavError: If the service refuses the upload for any other reason.
    """
    request_path = webdav_path(project, rel_path)
    headers = {"X-OC-Mtime": str(mtime)}
    if create_only:
        headers["If-None-Match"] = "*"

    try:
        response = await _request_with_rate_limit_retry(
            client, "PUT", request_path, content=content, headers=headers
        )
        # Checked before raise_for_status: a refused precondition is the answer
        # this call asked for, not a failure.
        if create_only and response.status_code == httpx.codes.PRECONDITION_FAILED:
            return False
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WebdavError(f"Failed to upload {rel_path}: {_describe(exc)}") from exc

    return True


# --- PROPFIND parsing ---


def _is_finite_depth_refusal(xml_text: str) -> bool:
    """Whether a 403 body is the RFC 4918 refusal of an infinite-depth PROPFIND."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return False
    return root.find(f"{DAV_NS}propfind-finite-depth") is not None


def _parse_propfind(xml_text: str, *, request_path: str) -> list[_Entry]:
    """Turn a recursive multistatus document into the collection's descendants.

    Each entry's project-relative path comes from its href, because a nested
    entry's display name is only its basename. The document is served by the
    authenticated cloud service, not by arbitrary third parties, so it is parsed
    with the standard library parser.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise WebdavError(f"Could not parse the cloud listing for '{request_path}': {exc}") from exc

    entries: list[_Entry] = []
    for index, response in enumerate(root.findall(f"{DAV_NS}response")):
        href = _text(response.find(f"{DAV_NS}href"))
        if href is None:
            raise WebdavError(
                f"The cloud listing for '{request_path}' contains an entry with no href"
            )

        # Trigger: the first response element describes the collection we asked
        # for (RFC 4918 includes the resource itself in a listing).
        # Why: only the first is checked; any later entry naming the collection
        # again is malformed and is rejected by _relative_path below.
        # Outcome: skip the self entry, keep everything else.
        if index == 0 and _same_path(href, request_path):
            continue

        rel_path = _relative_path(href, request_path)
        props = _merged_props(response)

        # The display name is redundant with the href's last segment. A mismatch
        # means the href was not percent-encoded the way it must be, so the path
        # it yields cannot be trusted to name the right file.
        display_name = _text(props.get("displayname"))
        if display_name and display_name != rel_path.rsplit("/", 1)[-1]:
            raise WebdavError(
                f"The cloud listing for '{request_path}' names {display_name!r} "
                f"at an href that decodes to {rel_path!r}"
            )

        entries.append(
            _Entry(
                rel_path=rel_path,
                is_collection=_is_collection(props),
                size=_parse_size(props),
                etag=normalize_etag(_text(props.get("getetag"))),
                modified=_parse_http_date(_text(props.get("getlastmodified"))),
            )
        )

    return entries


def _relative_path(href: str, request_path: str) -> str:
    """Return the path an href names relative to the collection that was listed.

    Raises:
        WebdavError: If the href lies outside that collection, or names the
            collection itself.
    """
    collection = unquote(request_path).rstrip("/") + "/"
    path = _href_path(href)
    if not path.startswith(collection):
        raise WebdavError(f"The cloud listing for '{request_path}' names {href!r}, outside it")
    rel_path = path.removeprefix(collection).strip("/")
    if not rel_path:
        raise WebdavError(f"The cloud listing for '{request_path}' repeats the collection itself")
    return rel_path


def _require_recursive_listing(entries: list[_Entry], project: str) -> None:
    """Refuse a listing that only describes the top level of the project.

    The service derives every collection in a recursive listing from the files
    beneath it, so each collection there has at least one listed file. A service
    that ignores ``Depth: infinity`` answers with one level: subdirectories with
    nothing listed inside them. Planning from that would treat every nested
    cloud file as absent.

    Raises:
        WebdavError: If a collection in the listing has no listed file beneath it.
    """
    directories_with_files = {
        parent.as_posix()
        for entry in entries
        if not entry.is_collection
        for parent in PurePosixPath(entry.rel_path).parents
    }
    empty = [
        entry.rel_path
        for entry in entries
        if entry.is_collection and entry.rel_path not in directories_with_files
    ]
    if empty:
        raise WebdavError(
            f"The cloud answered the listing of project '{project}' one level deep, so it "
            "does not yet support the recursive listing (Depth: infinity) this version "
            "of bm needs. Retry after the cloud service has been updated."
        )


def _merged_props(response: ElementTree.Element) -> dict[str, ElementTree.Element]:
    """Collect the properties of one response element, keyed by local tag name.

    Propstat blocks are merged without inspecting their status: a non-2xx block
    carries empty property elements, which read as "absent" anyway, so filtering
    on status would only add a branch that changes nothing.
    """
    props: dict[str, ElementTree.Element] = {}
    for propstat in response.findall(f"{DAV_NS}propstat"):
        prop = propstat.find(f"{DAV_NS}prop")
        if prop is None:
            continue
        for child in prop:
            props.setdefault(child.tag.removeprefix(DAV_NS), child)
    return props


def _is_collection(props: dict[str, ElementTree.Element]) -> bool:
    resource_type = props.get("resourcetype")
    if resource_type is None:
        return False
    return resource_type.find(f"{DAV_NS}collection") is not None


def _parse_size(props: dict[str, ElementTree.Element]) -> int:
    """Read getcontentlength, treating an absent or empty value as zero bytes."""
    raw = _text(props.get("getcontentlength"))
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError as exc:
        raise WebdavError(f"The cloud reported a non-numeric file size: {raw!r}") from exc


def _parse_http_date(raw: str | None) -> datetime | None:
    """Parse an RFC 1123 HTTP-date into an aware datetime, or None when absent."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError) as exc:
        raise WebdavError(f"The cloud reported an unparseable timestamp: {raw!r}") from exc


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip()


def _href_path(href: str) -> str:
    """Return the decoded path component of an href.

    RFC 4918 hrefs are URIs, so percent-encoding is decoded here before the path
    is compared or split into segments.
    """
    return unquote(urlsplit(href).path)


def _same_path(href: str, request_path: str) -> bool:
    """Compare an href against a request path, ignoring a trailing slash.

    Both sides are percent-decoded first: the request path is encoded by
    ``webdav_path`` while the href may or may not be, and the comparison is
    about which resource is named, not how it was spelled on the wire.
    """
    return _href_path(href).rstrip("/") == unquote(request_path).rstrip("/")


def _describe(exc: httpx.HTTPError) -> str:
    """Render an httpx failure as a single actionable line."""
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP {exc.response.status_code} - {exc.response.text.strip()}"
        return f"{detail}{_rate_limit_detail(exc.response)}"
    return str(exc)


def _rate_limit_detail(response: httpx.Response) -> str:
    """Append the rate-limit headers to a 429, or nothing for any other status.

    A rate-limited transfer is the one failure a user can act on, by retrying
    later or by moving less at once, but only if they can see it. The body alone
    does not say what the limit was or when it resets, which left the headers
    reachable only by editing this file (#2039).
    """
    if response.status_code != httpx.codes.TOO_MANY_REQUESTS:
        return ""

    reported = [
        (label, response.headers.get(header))
        for label, header in (
            ("limit", "X-RateLimit-Limit"),
            ("remaining", "X-RateLimit-Remaining"),
            ("retry after", "Retry-After"),
            ("resets at", "X-RateLimit-Reset"),
        )
    ]
    known = [f"{label} {value}" for label, value in reported if value is not None]
    if not known:
        return ""
    return f" (rate limit: {', '.join(known)})"
