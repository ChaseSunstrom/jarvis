"""PDFs and Word files, as text a model can read.

A page is not the only thing worth reading. A manual, a bill, a council letter
— the answer is in a document, and until now this service could only tell you
that chromium refused to render one.

## Why not Docling

Docling is the obvious choice and it was measured before it was rejected:
``pip install docling`` resolves to **101 packages**, including torch,
torchvision, transformers, opencv and the entire CUDA stack (cublas, cudnn,
nccl, cusparse, nvshmem). This host has no GPU and about 350 MB of free RAM.
Paying gigabytes of GPU libraries to read a text-layer PDF is not a trade, it
is a mistake with a citation. `docs/TOOLING_DECISIONS.md` records the numbers.

What is here instead costs one pure-Python wheel:

* **PDF** — `pypdf`, which reads the text layer. It does NOT do OCR: a scanned
  page is an image and comes back empty, and this module says so out loud
  rather than returning "" and letting a model invent the contents.
* **DOCX** — the standard library. A .docx is a zip of XML; the paragraphs are
  ``w:p`` elements and the tables are ``w:tbl``. Thirty lines and no
  dependency at all.

## What the caller gets

The same shape as a page: markdown-ish text, headings where the document has
them, and tables as markdown rows — because a table flattened into a column of
cells is a table whose meaning is gone, which is the same fix the HTML
extractor needed.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

#: Content types and extensions this module can read.
PDF_TYPES = ("application/pdf", "application/x-pdf")
DOCX_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)

#: The Word namespace. Every element below lives in it.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentError(RuntimeError):
    """The bytes were a document of this kind and could not be read."""


def kind_of(url: str = "", content_type: str = "") -> str:
    """``"pdf"`` | ``"docx"`` | ``""`` — what this is, by type then by name.

    Content type first because it is what the server says; the extension is the
    fallback for a server that says `application/octet-stream`, which many do.
    """
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in PDF_TYPES:
        return "pdf"
    if ctype in DOCX_TYPES:
        return "docx"
    # A server that states some OTHER type is believed: `report.pdf` answering
    # with `text/html` is an error page or a login wall, and reading it as a
    # PDF reports "malformed document" when the truth is "404".
    if ctype and ctype not in ("application/octet-stream", "binary/octet-stream"):
        return ""
    path = (url or "").split("?")[0].split("#")[0].lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith(".docx"):
        return "docx"
    return ""


def _table(rows: list[list[str]]) -> list[str]:
    """Markdown rows, with the rule under the first one."""
    if not rows:
        return []
    width = max(len(row) for row in rows)
    out = []
    for index, row in enumerate(rows):
        cells = [cell.replace("|", "\\|") for cell in row] + [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
        if index == 0:
            out.append("| " + " | ".join("---" for _ in cells) + " |")
    return out


def docx_to_text(data: bytes) -> str:
    """A .docx's paragraphs and tables, in order, as markdown-ish text."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        xml = archive.read("word/document.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as err:
        raise DocumentError(f"not a readable .docx: {err}") from err
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as err:
        raise DocumentError(f"the document's XML is malformed: {err}") from err

    def text_of(node) -> str:
        return "".join(part.text or "" for part in node.iter(f"{_W}t")).strip()

    def style_of(node) -> str:
        style = node.find(f"{_W}pPr/{_W}pStyle")
        return (style.get(f"{_W}val") or "") if style is not None else ""

    body = root.find(f"{_W}body")
    lines: list[str] = []
    for node in list(body) if body is not None else []:
        if node.tag == f"{_W}p":
            text = text_of(node)
            if not text:
                continue
            # Word records a heading as a style name, `Heading1`…`Heading9`.
            level = re.match(r"Heading(\d)", style_of(node))
            lines.append(f"{'#' * int(level.group(1))} {text}" if level else text)
        elif node.tag == f"{_W}tbl":
            rows = [
                [text_of(cell) for cell in row.findall(f"{_W}tc")]
                for row in node.findall(f"{_W}tr")
            ]
            # ONE block, joined by single newlines: paragraphs are separated
            # by a blank line below, and a blank line between table rows is a
            # markdown table that renders as prose.
            table = _table([row for row in rows if any(row)])
            if table:
                lines.append("\n".join(table))
    return "\n\n".join(lines).strip()


def pdf_to_text(data: bytes) -> str:
    """A PDF's text layer, page by page.

    Raises rather than returning "" when there is no text layer at all: an
    empty string reaching a model is an invitation to make the contents up,
    and "this PDF is scanned images" is a true and useful answer.
    """
    try:
        from pypdf import PdfReader
    except ImportError as err:  # pragma: no cover - environment dependent
        raise DocumentError(
            "pypdf is not installed in this container, so PDFs cannot be read"
        ) from err
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as err:  # noqa: BLE001 - every malformed PDF, named
        raise DocumentError(f"the PDF could not be read: {type(err).__name__}") from err
    if not any(pages):
        raise DocumentError(
            f"this PDF has {len(pages)} page(s) and no text layer — it is "
            "probably scanned images, which this service does not OCR"
        )
    out = []
    for number, text in enumerate(pages, 1):
        if text:
            out.append(f"## Page {number}\n\n{text}")
    return "\n\n".join(out).strip()


def to_text(data: bytes, kind: str) -> str:
    if kind == "pdf":
        return pdf_to_text(data)
    if kind == "docx":
        return docx_to_text(data)
    raise DocumentError(f"unknown document kind {kind!r}")
