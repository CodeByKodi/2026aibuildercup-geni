from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfWriter

from workpilot import document_ai, documents
from workpilot.documents import LocalDocumentProcessor, process_document, source_id_for
from workpilot.models import DocumentChunk


def test_text_markdown_csv_parse():
    for name in ("notes.txt", "notes.md", "data.csv"):
        chunks, parser = process_document(name, b"alpha beta gamma")
        assert len(chunks) == 1
        assert chunks[0].text == "alpha beta gamma"
        assert chunks[0].page == 1
        assert chunks[0].word_count == 3
        assert parser == "Native text parser"
        assert chunks[0].source_id == source_id_for(name)


def test_docx_parse():
    doc = Document()
    doc.add_paragraph("Decision: proceed with cache optimization")
    buf = BytesIO()
    doc.save(buf)
    chunks, parser = process_document("meeting.docx", buf.getvalue())
    assert "cache optimization" in chunks[0].text
    assert parser == "python-docx"


def test_blank_pdf_without_text_layer_raises_clear_error():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    with pytest.raises(ValueError, match="Document AI"):
        process_document("scan.pdf", buf.getvalue())


def test_image_without_document_ai_raises_clear_error():
    with pytest.raises(ValueError, match="Document AI"):
        process_document("photo.png", b"\x89PNG")


def test_unsupported_and_oversize_files():
    with pytest.raises(ValueError, match="Unsupported"):
        process_document("script.exe", b"MZ")
    with pytest.raises(ValueError, match="upload limit"):
        process_document("big.txt", b"x" * (documents.MAX_UPLOAD_BYTES + 1))


def test_document_ai_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(document_ai, "configured", lambda: True)

    def boom(self, *args, **kwargs):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(document_ai.DocumentAIProcessor, "process", boom)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = BytesIO()
    writer.write(buf)
    # Local PDF parser also finds no text, so we expect the clear local error, not a Document AI crash.
    with pytest.raises(ValueError, match="text layer"):
        process_document("scan.pdf", buf.getvalue())
    # For a text file the Document AI path is never attempted.
    chunks, parser = process_document("ok.md", b"hello world")
    assert parser == "Native text parser" and chunks[0].text == "hello world"


def test_document_ai_success_returns_page_chunks(monkeypatch):
    monkeypatch.setattr(document_ai, "configured", lambda: True)
    from workpilot.models import DocumentChunk

    def fake(self, sid, name, raw, mime):
        return [DocumentChunk(sid, name, "page one", 1, {"parser": "Google Document AI"}), DocumentChunk(sid, name, "page two", 2)]

    monkeypatch.setattr(document_ai.DocumentAIProcessor, "process", fake)
    chunks, parser = process_document("scan.pdf", b"%PDF-1.4")
    assert parser == "Google Document AI"
    assert [c.page for c in chunks] == [1, 2]
    assert documents.chunks_to_text(chunks) == "page one\n\npage two"


def test_local_processor_supports():
    proc = LocalDocumentProcessor()
    assert proc.supports(".md") and proc.supports(".pdf") and not proc.supports(".png")


def test_scanned_pdf_fixture_needs_document_ai_and_is_parsed_by_it(monkeypatch, tmp_path):
    from pathlib import Path

    raw = (Path(__file__).resolve().parents[1] / "sample_data" / "scanned" / "cab_minutes_scanned.pdf").read_bytes()
    with pytest.raises(ValueError, match="Document AI"):
        documents.process_document("cab_minutes_scanned.pdf", raw)  # no text layer, OCR off

    seen = {}

    def ocr(self, source_id, source_name, data, mime_type):
        seen["mime"] = mime_type
        return [DocumentChunk(source_id, source_name, "CAB-77: Rollback rehearsal completed.", 1, {"parser": self.name})]

    monkeypatch.setattr(document_ai, "configured", lambda: True)
    monkeypatch.setattr(document_ai.DocumentAIProcessor, "process", ocr)
    chunks, parser = documents.process_document("cab_minutes_scanned.pdf", raw)
    assert parser == "Google Document AI" and seen["mime"] == "application/pdf"
    assert "CAB-77" in chunks[0].text


