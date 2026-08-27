"""Documents, read without a gigabyte of GPU libraries.

`documents.py` exists because `pip install docling` resolves to 101 packages
including torch and the CUDA stack, on a host with no GPU. What replaces it has
to actually work, and these are the cases that decide that: a PDF with a text
layer, one without, a .docx with a table, and files that are neither.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from jarvis_browser.documents import (
    DocumentError,
    docx_to_text,
    kind_of,
    pdf_to_text,
    to_text,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(body: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
        )
    return out.getvalue()


def para(text: str, style: str = "") -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>"


def test_the_type_the_server_states_beats_the_extension():
    """A `.pdf` that answers with HTML is a 404 page, not a malformed PDF."""
    assert kind_of("http://x/y.pdf", "text/html") == ""
    assert kind_of("http://x/y", "application/pdf") == "pdf"
    assert kind_of("http://x/report.pdf?v=2#page") == "pdf"
    assert kind_of("http://x/notes.docx") == "docx"
    assert kind_of("http://x/page.html") == ""
    assert kind_of("") == ""


def test_a_word_heading_survives_as_a_heading():
    text = docx_to_text(docx(para("Service record", "Heading1") + para("Serviced in March.")))
    assert text.startswith("# Service record")
    assert "Serviced in March." in text


def test_a_word_table_keeps_its_rows():
    """The failure this replaced: cells in a column with nothing to attach them to."""
    rows = (
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>Year</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Part</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>2023</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Expansion vessel</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
    )
    text = docx_to_text(docx(rows))
    assert "| Year | Part |" in text
    assert "| --- | --- |" in text
    assert "| 2023 | Expansion vessel |" in text
    # One block: a blank line between rows renders as prose, not a table.
    assert "|\n\n|" not in text


def test_a_pipe_in_a_cell_cannot_end_the_column():
    rows = (
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>a|b</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    assert r"a\|b" in docx_to_text(docx(rows))


def test_something_that_is_not_a_docx_says_so():
    with pytest.raises(DocumentError) as err:
        docx_to_text(b"not a zip at all")
    assert "readable .docx" in str(err.value)


def test_a_zip_with_no_document_in_it_says_so():
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(DocumentError):
        docx_to_text(empty.getvalue())


def test_a_scanned_pdf_is_named_rather_than_returned_empty():
    """An empty string reaching a model is an invitation to invent the contents."""
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>\nendobj\n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\n%%EOF\n"
    )
    with pytest.raises(DocumentError) as err:
        pdf_to_text(pdf)
    assert "text layer" in str(err.value) or "could not be read" in str(err.value)


def test_the_repositorys_own_fixture_pdf_reads():
    """The one the live suite asks Jarvis about, so a change here is caught here."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "testing/live/fixtures/handbook/warranty.pdf"
    text = pdf_to_text(path.read_bytes())
    assert "seven year parts and labour warranty" in text
    assert "0800 496 0114" in text
    assert text.startswith("## Page 1")


def test_an_unknown_kind_is_refused_rather_than_guessed():
    with pytest.raises(DocumentError):
        to_text(b"anything", "epub")
