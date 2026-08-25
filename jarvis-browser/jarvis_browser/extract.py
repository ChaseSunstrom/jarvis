"""HTML -> text/markdown, links and metadata. Stdlib only, no network.

Readability-ish rather than readability: we drop the tags that never carry
the page's point (script, style, nav, footer, aside), keep block structure as
newlines, collapse whitespace and cap the result. Deliberately dependency
light — this parses hostile input, so a small, boring parser beats a clever
one with a CVE feed.

Nothing here trusts its input. Callers must still pass the result through
``safety.fence`` before it reaches a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .safety import strip_url_credentials

# Content of these never becomes text. NOTE: <head> is deliberately absent —
# we still need <title>, <meta> and <base> from it. Stray text inside head is
# suppressed by the _in_head flag instead.
_SKIP_CONTENT_TAGS = frozenset({
    "script", "style", "noscript", "template", "svg", "canvas", "math",
    "iframe", "object", "embed", "applet", "select", "datalist",
})

# Page chrome: present on every page, never the answer.
_CHROME_TAGS = frozenset({"nav", "footer", "aside", "menu"})

# Tags that force a line break in the extracted text.
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "main", "header", "footer", "nav",
    "aside", "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody",
    "tr", "td", "th", "form", "fieldset", "figure", "figcaption",
    "blockquote", "pre", "hr", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "address", "details", "summary",
})

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})

# Longest href we will hand back. `check_url` refuses anything past this, and
# the crawler runs caller-supplied regexes over every link it collects — an
# unbounded URL from a hostile page is free CPU for a catastrophic pattern.
MAX_LINK_URL_CHARS = 4096

_KEEP_META = frozenset({
    "description", "author", "keywords", "og:title", "og:description",
    "og:site_name", "article:published_time",
})


@dataclass
class Link:
    url: str
    text: str

    def as_dict(self) -> dict[str, str]:
        return {"url": self.url, "text": self.text}


@dataclass
class PageExtract:
    url: str = ""
    title: str = ""
    text: str = ""
    links: list[Link] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    char_count: int = 0


class _Extractor(HTMLParser):
    def __init__(self, *, drop_chrome: bool = True, markdown: bool = True):
        super().__init__(convert_charrefs=True)
        self.drop_chrome = drop_chrome
        self.markdown = markdown
        self.chunks: list[str] = []
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.base_href: str | None = None
        self.raw_links: list[tuple[str, str]] = []
        # Stack of tag names whose content we are discarding. A stack (not a
        # counter) so malformed nesting degrades gracefully.
        self._skip_stack: list[str] = []
        self._in_head = False
        self._in_title = False
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self._pending_heading: int | None = None
        # Tables, buffered a row at a time. A cell's text has to be held until
        # the row ends, because a table's meaning is which cell sits under
        # which heading — and this extractor used to emit one cell per line,
        # which turns a tariff into a column of numbers with nothing to attach
        # them to. Measured on the fixture handbook's rate table: every figure
        # survived and no row did.
        self._table_depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._rows_in_table = 0

    # -- helpers -----------------------------------------------------------
    @property
    def _skipping(self) -> bool:
        return bool(self._skip_stack)

    def _emit(self, text: str) -> None:
        if not text:
            return
        if self._cell is not None:
            self._cell.append(text)
            return
        self.chunks.append(text)

    def _end_row(self) -> None:
        """One buffered row, as a line somebody (or a model) can read."""
        cells = self._row or []
        self._row = None
        if not cells:
            return
        # `|` inside a cell would end it early in markdown, and a stray one in
        # plain text reads as a column that is not there.
        clean = [cell.replace("|", "\\|") for cell in cells]
        self._rows_in_table += 1
        self.chunks.append("\n| " + " | ".join(clean) + " |")
        if self.markdown and self._rows_in_table == 1:
            # Markdown needs the rule under the first row or the whole thing
            # renders as one paragraph.
            self.chunks.append("\n| " + " | ".join("---" for _ in clean) + " |")

    def _should_skip(self, tag: str) -> bool:
        if tag in _SKIP_CONTENT_TAGS:
            return True
        return self.drop_chrome and tag in _CHROME_TAGS

    # -- HTMLParser hooks --------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}

        if self._should_skip(tag):
            if tag not in _VOID_TAGS:
                self._skip_stack.append(tag)
            return

        if tag == "base" and "href" in attr and self.base_href is None:
            self.base_href = attr["href"].strip()
            return

        if tag == "meta":
            key = (attr.get("name") or attr.get("property") or "").lower()
            content = attr.get("content", "").strip()
            if key in _KEEP_META and content and key not in self.meta:
                self.meta[key] = content[:500]
            return

        if self._skipping:
            return

        if tag == "head":
            self._in_head = True
            return

        if tag == "title":
            self._in_title = True
            return

        if tag == "a":
            href = attr.get("href", "").strip()
            self._link_href = href or None
            self._link_text = []

        if tag == "img":
            alt = attr.get("alt", "").strip()
            if alt:
                self._emit(f"[image: {alt[:200]}]")
            return

        if tag in _HEADINGS:
            self._emit("\n\n")
            self._pending_heading = _HEADINGS[tag]
            return

        if tag == "li":
            self._emit("\n")
            if self.markdown:
                self._emit("- ")
            return

        if tag == "table":
            self._table_depth += 1
            self._rows_in_table = 0
            self.chunks.append("\n")
            return

        if tag == "tr" and self._table_depth:
            self._row = []
            return

        if tag in ("thead", "tbody", "tfoot") and self._table_depth:
            # Grouping elements, not content. Letting them fall through to the
            # block-tag newline put a blank line between the header row and the
            # rule under it, which is a markdown table that renders as prose.
            return

        if tag in ("td", "th") and self._table_depth:
            self._cell = []
            return

        if tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if self._skip_stack and self._skip_stack[-1] == tag:
            self._skip_stack.pop()
            return
        if tag in self._skip_stack:
            # Malformed nesting: unwind to the matching opener.
            while self._skip_stack and self._skip_stack[-1] != tag:
                self._skip_stack.pop()
            if self._skip_stack:
                self._skip_stack.pop()
            return
        if self._skipping:
            return

        if tag == "head":
            self._in_head = False
            return

        if tag == "title":
            self._in_title = False
            return

        if tag == "a":
            href, self._link_href = self._link_href, None
            text = " ".join("".join(self._link_text).split())[:200]
            self._link_text = []
            if href:
                self.raw_links.append((href, text))
            return

        if tag in ("td", "th") and self._cell is not None:
            cell = " ".join("".join(self._cell).split())
            self._cell = None
            if self._row is None:
                self._row = []
            self._row.append(cell)
            return

        if tag == "tr" and self._table_depth:
            self._end_row()
            return

        if tag in ("thead", "tbody", "tfoot") and self._table_depth:
            return

        if tag == "table" and self._table_depth:
            # A `</table>` with a row still open: malformed markup, and the row
            # is still worth having.
            self._end_row()
            self._table_depth = max(0, self._table_depth - 1)
            self._rows_in_table = 0
            self.chunks.append("\n")
            return

        if tag in _HEADINGS:
            self._pending_heading = None
            self._emit("\n\n")
            return

        if tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._skipping or not data:
            return
        if self._table_depth and self._cell is None and not data.strip():
            # The newlines and indentation BETWEEN a table's cells are markup,
            # not content: emitted, they became a blank line between every row.
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._in_head:
            return  # stray text in <head> is never page content
        if self._pending_heading is not None and data.strip():
            if self.markdown:
                self._emit("#" * self._pending_heading + " ")
            self._pending_heading = None
        if self._link_href is not None:
            self._link_text.append(data)
        self._emit(data)

    # Comments and declarations are dropped: HTMLParser only surfaces them
    # through handle_comment/handle_decl, which we deliberately do not
    # implement.


def _collapse(text: str) -> str:
    """Collapse intra-line whitespace and runs of blank lines."""
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out: list[str] = []
    blanks = 0
    for line in lines:
        if line:
            blanks = 0
            out.append(line)
        else:
            blanks += 1
            if blanks <= 1 and out:
                out.append("")
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def extract(
    html: str,
    *,
    base_url: str = "",
    max_chars: int = 40_000,
    max_links: int = 200,
    drop_chrome: bool = True,
    markdown: bool = True,
) -> PageExtract:
    """Parse ``html`` into title, text, links and metadata.

    ``max_chars`` caps the returned text (a hostile page is unbounded);
    ``truncated`` says whether the cap bit.
    """
    parser = _Extractor(drop_chrome=drop_chrome, markdown=markdown)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # A malformed document must degrade to "whatever we parsed so far",
        # never to a 500. HTMLParser is lenient but not infallible.
        pass

    title = " ".join("".join(parser.title_parts).split())[:300]
    if not title:
        title = parser.meta.get("og:title", "")[:300]

    text = _collapse("".join(parser.chunks))
    full_len = len(text)
    truncated = full_len > max_chars
    if truncated:
        text = text[:max_chars].rstrip() + "\n…[truncated]"

    base = parser.base_href or base_url or ""
    links = extract_links(
        parser.raw_links, base_url=base, max_links=max_links
    )

    return PageExtract(
        url=base_url,
        title=title,
        text=text,
        links=links,
        meta=dict(parser.meta),
        truncated=truncated,
        char_count=full_len,
    )


def extract_links(
    raw: list[tuple[str, str]], *, base_url: str = "", max_links: int = 200
) -> list[Link]:
    """Resolve, filter and dedupe hrefs.

    Only http(s) survives: ``javascript:``, ``data:`` and friends are exactly
    the schemes an injected page would use to get something executed.
    """
    seen: set[str] = set()
    out: list[Link] = []
    for href, text in raw:
        href = href.strip()
        if not href or href.startswith("#"):
            continue
        if len(href) > MAX_LINK_URL_CHARS:
            continue
        try:
            resolved = urljoin(base_url, href) if base_url else href
        except ValueError:
            continue
        try:
            scheme = (urlsplit(resolved).scheme or "").lower()
        except ValueError:
            continue
        if scheme not in ("http", "https"):
            continue
        resolved = strip_url_credentials(resolved)
        if len(resolved) > MAX_LINK_URL_CHARS:
            continue
        key = resolved.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(Link(url=key, text=text))
        if len(out) >= max_links:
            break
    return out


def links_from_html(
    html: str, *, base_url: str = "", max_links: int = 200
) -> list[Link]:
    """Convenience wrapper used by the crawler."""
    return extract(
        html, base_url=base_url, max_chars=1, max_links=max_links
    ).links
