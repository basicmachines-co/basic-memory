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

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
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


async def list_project_files(client: httpx.AsyncClient, project: str) -> list[RemoteFile]:
    """Enumerate every file in a cloud project.

    Constraint: PROPFIND answers for one collection at a time (a ``Depth: 1``
    listing), so a whole-project listing is a walk. Subdirectories are visited
    breadth-first, and each path is listed only once, so a response that repeats
    a directory already walked cannot send the walk round in circles.

    Raises:
        WebdavError: If the service rejects a listing or returns XML we cannot
            interpret.
    """
    files: list[RemoteFile] = []
    pending = [""]  # project-relative directories; "" is the project root
    visited: set[str] = set()

    while pending:
        rel_dir = pending.pop(0)
        if rel_dir in visited:
            continue
        visited.add(rel_dir)

        for entry in await _propfind(client, project, rel_dir):
            if entry.is_collection:
                pending.append(entry.rel_path)
            else:
                files.append(
                    RemoteFile(
                        path=entry.rel_path,
                        size=entry.size,
                        etag=entry.etag,
                        modified=entry.modified,
                    )
                )

    return files


async def download_file(client: httpx.AsyncClient, project: str, rel_path: str) -> DownloadedFile:
    """Fetch one file, along with the last-modified time the service reports.

    Raises:
        WebdavError: If the service refuses the download.
    """
    request_path = webdav_path(project, rel_path)
    try:
        response = await client.get(request_path)
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
        response = await client.put(request_path, content=content, headers=headers)
        # Checked before raise_for_status: a refused precondition is the answer
        # this call asked for, not a failure.
        if create_only and response.status_code == httpx.codes.PRECONDITION_FAILED:
            return False
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WebdavError(f"Failed to upload {rel_path}: {_describe(exc)}") from exc

    return True


# --- PROPFIND parsing ---


async def _propfind(client: httpx.AsyncClient, project: str, rel_dir: str) -> list[_Entry]:
    """List one collection, returning its immediate children."""
    request_path = webdav_path(project, rel_dir)
    try:
        response = await client.request(
            "PROPFIND",
            request_path,
            content=_PROPFIND_BODY,
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WebdavError(f"Failed to list cloud project '{project}': {_describe(exc)}") from exc

    return _parse_propfind(response.text, request_path=request_path, rel_dir=rel_dir)


def _parse_propfind(xml_text: str, *, request_path: str, rel_dir: str) -> list[_Entry]:
    """Turn a multistatus document into this collection's immediate children.

    The document is served by the authenticated cloud service, not by arbitrary
    third parties, so it is parsed with the standard library parser.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise WebdavError(f"Could not parse the cloud listing for '{request_path}': {exc}") from exc

    entries: list[_Entry] = []
    for index, response in enumerate(root.findall(f"{DAV_NS}response")):
        href = _text(response.find(f"{DAV_NS}href"))

        # Trigger: the first response element describes the collection we asked
        # for (RFC 4918 includes the resource itself in a Depth: 1 listing).
        # Why: only the first is checked — a subdirectory whose href happens to
        # collide with the request path is still a real child, and dropping it
        # would silently hide every file beneath it.
        # Outcome: skip the self entry, keep everything else.
        if index == 0 and href is not None and _same_path(href, request_path):
            continue

        props = _merged_props(response)
        name = _entry_name(props, href)
        if name is None:
            raise WebdavError(
                f"The cloud listing for '{request_path}' contains an entry with no name"
            )

        entries.append(
            _Entry(
                rel_path=f"{rel_dir}/{name}" if rel_dir else name,
                is_collection=_is_collection(props),
                size=_parse_size(props),
                etag=normalize_etag(_text(props.get("getetag"))),
                modified=_parse_http_date(_text(props.get("getlastmodified"))),
            )
        )

    return entries


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


def _entry_name(props: dict[str, ElementTree.Element], href: str | None) -> str | None:
    """Resolve an entry's basename.

    ``displayname`` is preferred because it is the literal name, free of any URL
    encoding. The href's last segment is the fallback for servers that omit it.
    """
    display_name = _text(props.get("displayname"))
    if display_name:
        return display_name
    if href is None:
        return None
    segments = [segment for segment in _href_path(href).split("/") if segment]
    return segments[-1] if segments else None


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
        return f"HTTP {exc.response.status_code} - {exc.response.text.strip()}"
    return str(exc)
