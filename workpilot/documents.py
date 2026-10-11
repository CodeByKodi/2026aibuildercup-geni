"""Document processing.

    DocumentProcessor
        ├── DocumentAIProcessor   (PDF / scanned PDF / images → OCR)
        └── LocalDocumentProcessor (txt / md / csv / docx / pdf text layer)

Both return normalized `DocumentChunk` objects. A Document AI failure never
stops ingestion: the file is re-processed locally and the fallback is
reported to the caller so the UI can show it.
"""
import hashlib
import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Iterable, List, Protocol, Tuple

from pypdf import PdfReader
from docx import Document

from . import document_ai
from .models import DocumentChunk

log = logging.getLogger("workpilot.documents")

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
DOCAI_SUFFIXES = IMAGE_SUFFIXES | {".pdf"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def source_id_for(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "source"
    return f"{base}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"


class DocumentProcessor(Protocol):
    name: str

    def supports(self, suffix: str) -> bool: ...

    def process(self, source_id: str, source_name: str, raw: bytes, mime_type: str) -> List[DocumentChunk]: ...


class LocalDocumentProcessor:
    name = "Local parser"

    def supports(self, suffix: str) -> bool:
        return suffix in {".txt", ".md", ".csv", ".docx", ".pdf"}

    def process(self, source_id: str, source_name: str, raw: bytes, mime_type: str) -> List[DocumentChunk]:
        suffix = Path(source_name.lower()).suffix
        meta = {"parser": self.name, "mime_type": mime_type}
        if suffix in {".txt", ".md", ".csv"}:
            text = raw.decode("utf-8", errors="ignore")
            return [DocumentChunk(source_id, source_name, text, 1, {**meta, "parser": "Native text parser"})]
        if suffix == ".docx":
            doc = Document(BytesIO(raw))
            text = "\n".join(p.text for p in doc.paragraphs)
            return [DocumentChunk(source_id, source_name, text, 1, {**meta, "parser": "python-docx"})]
        if suffix == ".pdf":
            reader = PdfReader(BytesIO(raw))
            chunks = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    chunks.append(DocumentChunk(source_id, source_name, text, index, {**meta, "parser": "pypdf"}))
            if not chunks:
                raise ValueError("PDF has no extractable text layer (scanned PDF needs Document AI OCR).")
            return chunks
        if suffix in IMAGE_SUFFIXES:
            raise ValueError("Image OCR requires Document AI. Configure DOCUMENT_AI_PROCESSOR_ID.")
        raise ValueError(f"Unsupported file type: {source_name}")


def process_document(name: str, raw: bytes) -> Tuple[List[DocumentChunk], str]:
    """Process raw bytes into chunks. Returns (chunks, parser_label).

    Order: Document AI when configured and the format benefits from OCR,
    otherwise (or on Document AI failure) the local parser.
    """
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"{name} exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.")
    suffix = Path(name.lower()).suffix
    if suffix not in MIME_TYPES:
        raise ValueError(f"Unsupported file type: {name}")
    sid = source_id_for(name)
    mime = MIME_TYPES[suffix]
    fallback_note = ""

    if suffix in DOCAI_SUFFIXES and document_ai.configured():
        try:
            chunks = document_ai.DocumentAIProcessor().process(sid, name, raw, mime)
            if chunks:
                return chunks, document_ai.DocumentAIProcessor.name
            fallback_note = "Document AI returned no text"
        except Exception as exc:
            fallback_note = f"Document AI failed ({type(exc).__name__})"
            log.warning("Document AI failed for %s, falling back to local parser: %s", name, exc)

    chunks = LocalDocumentProcessor().process(sid, name, raw, mime)
    label = chunks[0].metadata.get("parser", LocalDocumentProcessor.name) if chunks else LocalDocumentProcessor.name
    if fallback_note:
        label = f"{label} (fallback: {fallback_note})"
    return chunks, label


def chunks_to_text(chunks: List[DocumentChunk]) -> str:
    return "\n\n".join(c.text for c in chunks)


def load_sample_documents(sample_dir: str) -> Iterable[Tuple[str, str]]:
    for path in sorted(Path(sample_dir).glob("*")):
        if path.suffix.lower() in {".md", ".txt"}:
            yield path.name, path.read_text(encoding="utf-8")


def word_count(text: str) -> int:
    return len(text.split())
