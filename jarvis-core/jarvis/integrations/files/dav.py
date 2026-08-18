"""Just enough WebDAV to list, read and write a file.

Nextcloud, ownCloud, Synology, Seafile, Box and a plain Apache `mod_dav` all
speak this, which is why it is the one protocol worth implementing for *"access
notes, docs, my cloud i can set"*. A Nextcloud-specific client would cover one.

Four requests, and no library:

    PROPFIND  Depth: 1   -> what is in this collection
    GET                  -> a file's bytes
    PUT                  -> write one
    MKCOL                -> make a collection

The XML is parsed with `xml.etree`, configured to refuse entity expansion —
see :func:`parse_listing`. Everything else about a response is treated as
untrusted, because a file's *name* is attacker-controlled on any share more
than one person can write to.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import httpx

_LOGGER = logging.getLogger(__name__)

__all__ = ["DavEntry", "DavError", "parse_listing", "propfind_body", "refuse_doctype"]

DAV_NS = "{DAV:}"

#: `Depth: 1` is this collection and its immediate children. `infinity` is a
#: request most servers refuse and all of them should — it is a whole-share
#: walk from one call.
DEPTH = "1"

MAX_ENTRIES = 500


class DavError(RuntimeError):
    """Anything a WebDAV server did that this cannot use."""


def propfind_body() -> str:
    """Ask for the four properties this needs, not for everything.

    An allprop PROPFIND on a Nextcloud share returns a great deal of per-file
    metadata nobody here reads, and on a large collection the difference is
    seconds.
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:propfind xmlns:d="DAV:">'
        "<d:prop>"
        "<d:resourcetype/>"
        "<d:getcontentlength/>"
        "<d:getlastmodified/>"
        "<d:getcontenttype/>"
        "</d:prop>"
        "</d:propfind>"
    )


@dataclass
class DavEntry:
    """One file or collection in a listing."""

    #: Path relative to the root, with no leading slash.
    path: str
    name: str
    is_dir: bool
    size: int = 0
    modified: str = ""
    content_type: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "is_dir": self.is_dir,
            "size": self.size,
            "modified": self.modified,
            "content_type": self.content_type,
        }


#: A multistatus body has no legitimate reason to declare a document type, and
#: a DTD is the only route to entity expansion in `xml.etree`.
_DOCTYPE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def refuse_doctype(xml: bytes | str) -> None:
    """Refuse a document that declares entities, before anything parses it.

    `xml.etree` does not resolve EXTERNAL entities, which is the file-read and
    SSRF half of XXE — but it expands internal ones perfectly happily, which is
    the billion-laughs half, and one PROPFIND reply can then cost the process
    its memory.

    The obvious mitigations do not exist here. `XMLParser().entity` is a
    read-only attribute on CPython and `.parser` is not exposed at all, so no
    handler can be installed; `defusedxml` is a dependency this image does not
    carry. What is left is refusing the construct, and for WebDAV that costs
    nothing: no server has a reason to send a DTD in a multistatus response.

    Checked on the RAW bytes, before any decoding, so an encoding that hides the
    keyword from a `str` comparison does not hide it from this.
    """
    raw = xml if isinstance(xml, bytes) else xml.encode("utf-8", "replace")
    if _DOCTYPE.search(raw):
        raise DavError(
            "the server's XML declares a document type or an entity; refusing to parse it"
        )


def parse_listing(xml: str, root_path: str) -> list[DavEntry]:
    """A multistatus body -> entries relative to `root_path`.

    `root_path` is the URL path of the collection that was asked about, so each
    href can be made relative to it. Servers differ on whether they return an
    absolute path, a full URL, and whether collections end in a slash; all three
    are normalised here rather than at three call sites.
    """
    refuse_doctype(xml)
    try:
        tree = ElementTree.fromstring(xml)
    except ElementTree.ParseError as err:
        raise DavError(f"the server did not answer with usable XML: {err}") from err

    # A `<html>` login page is perfectly well-formed XML. Parsed and then
    # searched for `d:response`, it yields nothing — and "no entries" is drawn
    # as an empty folder, so an expired session looks exactly like a share
    # somebody emptied. Checking the root element turns that into the sentence
    # the user needs.
    if tree.tag != f"{DAV_NS}multistatus":
        raise DavError(
            "the server did not answer with usable XML: expected a WebDAV "
            f"multistatus, got <{tree.tag}> — is the URL right and the login valid?"
        )

    base = unquote(root_path).rstrip("/")
    out: list[DavEntry] = []
    for response in tree.findall(f"{DAV_NS}response")[: MAX_ENTRIES + 1]:
        href_el = response.find(f"{DAV_NS}href")
        if href_el is None or not (href_el.text or "").strip():
            continue
        href = unquote(href_el.text.strip())
        # Some servers answer with a whole URL rather than a path.
        if "://" in href:
            href = urlsplit(href).path
        href = href.rstrip("/")
        if href == base:
            continue  # the collection itself, which every server includes
        if not href.startswith(base + "/"):
            # Not under what we asked about. A server doing this is either
            # confused or trying something; either way it is not ours to show.
            _LOGGER.debug("dav: ignoring an href outside the collection: %s", href)
            continue
        relative = href[len(base) + 1 :]
        if not relative:
            continue

        props = response.find(f"{DAV_NS}propstat/{DAV_NS}prop")
        is_dir = (
            props is not None
            and props.find(f"{DAV_NS}resourcetype/{DAV_NS}collection") is not None
        )
        out.append(
            DavEntry(
                path=relative,
                name=relative.rsplit("/", 1)[-1],
                is_dir=is_dir,
                size=_int(_text(props, "getcontentlength")),
                modified=_text(props, "getlastmodified"),
                content_type=_text(props, "getcontenttype"),
            )
        )
        if len(out) >= MAX_ENTRIES:
            break
    out.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return out


def _text(props: Any, tag: str) -> str:
    if props is None:
        return ""
    el = props.find(f"{DAV_NS}{tag}")
    return (el.text or "").strip() if el is not None else ""


def _int(raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def auth_for(username: str, password: str) -> Any:
    """Basic auth, or none. Digest is not supported and says so."""
    if username or password:
        return httpx.BasicAuth(username, password)
    return None
