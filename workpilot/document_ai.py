"""Google Document AI processor adapter.

`DocumentAIProcessor` is used for PDFs and images (OCR / layout). It is only
*configured* when the env flags and a processor ID are present; `verify()`
performs a one-time call to confirm the processor really exists so the status
view can say ACTIVE instead of merely "flag set".
"""
import logging
from typing import List, Optional, Tuple

from google.api_core.client_options import ClientOptions

from .config import CONFIG
from .models import DocumentChunk

log = logging.getLogger("workpilot.docai")

_VERIFIED: Optional[Tuple[bool, str]] = None


def configured() -> bool:
    return (
        not CONFIG.demo_mode
        and CONFIG.document_ai_enabled
        and bool(CONFIG.project_id)
        and bool(CONFIG.document_ai_processor_id)
    )


def _client():
    from google.cloud import documentai_v1 as documentai

    opts = ClientOptions(api_endpoint=f"{CONFIG.document_ai_location}-documentai.googleapis.com")
    return documentai, documentai.DocumentProcessorServiceClient(client_options=opts)


def _processor_name(client) -> str:
    return client.processor_path(CONFIG.project_id, CONFIG.document_ai_location, CONFIG.document_ai_processor_id)


def verify(force: bool = False) -> Tuple[bool, str]:
    """Check the processor exists. Cached per process."""
    global _VERIFIED
    if _VERIFIED is not None and not force:
        return _VERIFIED
    if not configured():
        _VERIFIED = (False, "not configured")
        return _VERIFIED
    try:
        _, client = _client()
        proc = client.get_processor(name=_processor_name(client))
        state = getattr(proc.state, "name", str(proc.state))
        ok = state == "ENABLED"
        _VERIFIED = (ok, f"{proc.display_name} ({proc.type_}, {state})")
    except Exception as exc:
        _VERIFIED = (False, f"{type(exc).__name__}: {str(exc)[:160]}")
    return _VERIFIED


def status() -> Tuple[str, str]:
    """ACTIVE / FALLBACK / NOT CONFIGURED with a truthful detail string."""
    if CONFIG.demo_mode:
        return "FALLBACK", "DEMO_MODE: local parsers only"
    if not CONFIG.document_ai_enabled or not CONFIG.document_ai_processor_id:
        return "NOT CONFIGURED", "Set DOCUMENT_AI_ENABLED=true and DOCUMENT_AI_PROCESSOR_ID"
    ok, detail = verify()
    if ok:
        return "ACTIVE", f"Processor {CONFIG.document_ai_processor_id}: {detail}"
    return "FALLBACK", f"Processor check failed, local parsers in use: {detail}"


class DocumentAIProcessor:
    name = "Google Document AI"

    def supports(self, suffix: str) -> bool:
        return suffix in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

    def process(self, source_id: str, source_name: str, raw: bytes, mime_type: str) -> List[DocumentChunk]:
        """Run OCR/layout extraction and return one chunk per page.

        Raises on cloud errors; the caller decides whether to fall back.
        """
        if not configured():
            raise RuntimeError("Document AI is not configured")
        documentai, client = _client()
        raw_document = documentai.RawDocument(content=raw, mime_type=mime_type)
        request = documentai.ProcessRequest(name=_processor_name(client), raw_document=raw_document)
        result = client.process_document(request=request)
        document = result.document
        full_text = document.text or ""
        chunks: List[DocumentChunk] = []
        for index, page in enumerate(document.pages, start=1):
            text = ""
            try:
                for segment in page.layout.text_anchor.text_segments:
                    text += full_text[int(segment.start_index) : int(segment.end_index)]
            except Exception:
                text = ""
            if text.strip():
                chunks.append(
                    DocumentChunk(
                        source_id=source_id,
                        source_name=source_name,
                        text=text,
                        page=index,
                        metadata={"parser": self.name, "mime_type": mime_type},
                    )
                )
        if not chunks and full_text.strip():
            chunks.append(
                DocumentChunk(source_id=source_id, source_name=source_name, text=full_text, page=1, metadata={"parser": self.name})
            )
        return chunks


