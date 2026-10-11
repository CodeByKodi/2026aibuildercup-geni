"""Vertex AI Search (Agent Search / Discovery Engine) adapter.

`status()` reports ACTIVE only after the engine has been looked up
successfully; a flag alone never produces an ACTIVE claim.
"""
import hashlib
import json
import logging
import re
import threading
from typing import Dict, List, Optional, Tuple

from google.api_core.client_options import ClientOptions

from .config import CONFIG
from .models import SourceChunk

log = logging.getLogger("workpilot.search")

_VERIFIED: Optional[Tuple[bool, str]] = None


def configured() -> bool:
    return (
        not CONFIG.demo_mode
        and CONFIG.vertex_search_enabled
        and bool(CONFIG.project_id)
        and bool(CONFIG.vertex_search_engine_id)
    )


def _client_options():
    if CONFIG.vertex_search_location == "global":
        return None
    return ClientOptions(api_endpoint=f"{CONFIG.vertex_search_location}-discoveryengine.googleapis.com")


def _engine_name() -> str:
    return (
        f"projects/{CONFIG.project_id}/locations/{CONFIG.vertex_search_location}/"
        f"collections/default_collection/engines/{CONFIG.vertex_search_engine_id}"
    )


def verify(force: bool = False) -> Tuple[bool, str]:
    """Confirm the engine exists. Cached per process."""
    global _VERIFIED
    if _VERIFIED is not None and not force:
        return _VERIFIED
    if not configured():
        _VERIFIED = (False, "not configured")
        return _VERIFIED
    try:
        from google.cloud import discoveryengine_v1 as discoveryengine

        client = discoveryengine.EngineServiceClient(client_options=_client_options())
        engine = client.get_engine(name=_engine_name())
        stores = ", ".join(engine.data_store_ids)
        _VERIFIED = (True, f"{engine.display_name} → data store(s): {stores}")
    except Exception as exc:
        _VERIFIED = (False, f"{type(exc).__name__}: {str(exc)[:160]}")
    return _VERIFIED


def status() -> Tuple[str, str]:
    """ACTIVE / FALLBACK / NOT CONFIGURED."""
    if CONFIG.demo_mode:
        return "FALLBACK", "DEMO_MODE: local TF-IDF retrieval"
    if not CONFIG.vertex_search_enabled or not CONFIG.vertex_search_engine_id:
        return "NOT CONFIGURED", "Set VERTEX_SEARCH_ENABLED=true and VERTEX_SEARCH_ENGINE_ID (scripts/bootstrap_cloud.py)"
    ok, detail = verify()
    if ok:
        return "ACTIVE", f"Engine {CONFIG.vertex_search_engine_id}: {detail}"
    return "FALLBACK", f"Engine check failed, local retrieval in use: {detail}"


def search(query: str, k: int = 6) -> List[SourceChunk]:
    if not configured():
        return []
    from google.cloud import discoveryengine_v1 as discoveryengine

    client = discoveryengine.SearchServiceClient(client_options=_client_options())
    serving_config = f"{_engine_name()}/servingConfigs/default_config"
    content_spec = discoveryengine.SearchRequest.ContentSearchSpec(
        snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(return_snippet=True),
        extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
            max_extractive_answer_count=2,
            max_extractive_segment_count=2,
        ),
    )
    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=k,
        content_search_spec=content_spec,
    )
    results = client.search(request=request)
    chunks: List[SourceChunk] = []
    for rank, item in enumerate(results):
        chunk = result_to_chunk(_document_to_dict(item.document), rank)
        if chunk:
            chunks.append(chunk)
        if len(chunks) >= k:
            break
    return chunks


def _document_to_dict(document) -> Dict:
    """Protobuf → plain dict (MapComposite values are not dicts)."""
    try:
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(document._pb, preserving_proto_field_name=True)
    except Exception:
        return {"id": getattr(document, "id", ""), "derived_struct_data": {}, "struct_data": {}}


def result_to_chunk(doc: Dict, rank: int = 0) -> Optional[SourceChunk]:
    """Build a SourceChunk from a plain-dict search result document."""
    derived = doc.get("derived_struct_data") or doc.get("derivedStructData") or {}
    struct = doc.get("struct_data") or doc.get("structData") or {}
    source = (
        derived.get("title")
        or struct.get("title")
        or derived.get("link")
        or struct.get("source")
        or doc.get("id")
        or "Agent Search result"
    )
    text = _best_text(derived)
    if not text:
        text = str(derived.get("description") or struct or "")
    if not text.strip():
        return None
    # Discovery Engine does not expose a relevance score here; rank is used so
    # the UI can still order evidence.
    score = round(max(0.0, 1.0 - rank * 0.1), 2)
    page = 1
    for key in ("extractive_segments", "extractiveSegments", "extractive_answers", "extractiveAnswers"):
        for entry in derived.get(key) or []:
            if isinstance(entry, dict):
                raw_page = entry.get("pageNumber") or entry.get("page_number") or entry.get("page")
                if raw_page:
                    try:
                        page = max(1, int(raw_page))
                        break
                    except (TypeError, ValueError):
                        continue
        if page > 1:
            break
    return SourceChunk(source=str(source), text=text[:4000], score=score, page=page, snippet=_display_snippet(derived))


def _display_snippet(derived: Dict) -> str:
    """Vertex's own answer passage, preferred over the (often whole-document) segment."""
    for key in ("extractive_answers", "extractiveAnswers"):
        for entry in derived.get(key) or []:
            value = entry.get("content") if isinstance(entry, dict) else ""
            if value:
                return str(value)
    for entry in derived.get("snippets") or []:
        if isinstance(entry, dict):
            snippet = entry.get("snippet") or ""
            if snippet and entry.get("snippet_status", entry.get("snippetStatus", "SUCCESS")) == "SUCCESS":
                return re.sub(r"<[^>]+>", "", snippet).replace("&nbsp;", " ")
    return ""


def _best_text(derived: Dict) -> str:
    parts: List[str] = []
    for key in ("extractive_segments", "extractiveSegments", "extractive_answers", "extractiveAnswers"):
        for entry in derived.get(key) or []:
            value = entry.get("content") if isinstance(entry, dict) else ""
            if value:
                parts.append(str(value))
    if parts:
        return "\n".join(dict.fromkeys(parts))
    for entry in derived.get("snippets") or []:
        if isinstance(entry, dict):
            snippet = entry.get("snippet") or entry.get("htmlSnippet") or ""
            if snippet and entry.get("snippet_status", entry.get("snippetStatus", "SUCCESS")) == "SUCCESS":
                return re.sub(r"<[^>]+>", "", snippet).replace("&nbsp;", " ")
    return ""


def _safe_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    base = re.sub(r"-+", "-", base)[:48] or "document"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"[:63]


def corpus_fingerprint(documents: Dict[str, str]) -> str:
    digest = hashlib.sha1()
    for name in sorted(documents):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha1(documents[name].encode("utf-8")).digest())
    return digest.hexdigest()


_INDEX_LOCK = threading.Lock()
_INDEX_SYNCING = False


def ensure_index(documents: Dict[str, str], force: bool = False, wait: bool = False) -> Dict[str, str]:
    """Keep Vertex Search aligned with the current workspace documents.

    Runs in the background unless `wait=True`. FULL import replaces stale
    documents so deleted sample files do not keep answering go-live questions.
    """
    global _INDEX_SYNCING
    if not configured() or not documents:
        return {"status": "skipped"}
    from . import state_store

    fingerprint = corpus_fingerprint(documents)
    stored = state_store.get_search_index_state() or {}
    if not force and stored.get("fingerprint") == fingerprint:
        return {"status": "current", "count": str(len(documents))}
    with _INDEX_LOCK:
        if _INDEX_SYNCING and not force:
            return {"status": "indexing"}
        _INDEX_SYNCING = True

    def _run() -> Dict[str, str]:
        global _INDEX_SYNCING
        try:
            count = sync_documents(documents)
            state_store.save_search_index_state(fingerprint, list(documents))
            return {"status": "current", "count": str(count)}
        except Exception as exc:
            log.warning("Vertex Search auto-index failed: %s", exc)
            return {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
        finally:
            with _INDEX_LOCK:
                _INDEX_SYNCING = False

    if wait:
        return _run()
    threading.Thread(target=_run, daemon=True, name="workpilot-vertex-index").start()
    return {"status": "started", "count": str(len(documents))}


def sync_documents(documents: Dict[str, str]) -> int:
    """Replace the Agent Search data store with the current workspace documents."""
    if not CONFIG.vertex_search_data_store_id:
        raise RuntimeError("VERTEX_SEARCH_DATA_STORE_ID is not configured")
    if CONFIG.demo_mode:
        raise RuntimeError("Disable DEMO_MODE before syncing to Agent Search")

    from google.cloud import discoveryengine_v1 as discoveryengine

    client = discoveryengine.DocumentServiceClient(client_options=_client_options())
    parent = client.branch_path(
        project=CONFIG.project_id,
        location=CONFIG.vertex_search_location,
        data_store=CONFIG.vertex_search_data_store_id,
        branch="default_branch",
    )

    items = [
        discoveryengine.Document(
            id=_safe_id(name),
            json_data=json.dumps({"title": name}),
            content=discoveryengine.Document.Content(raw_bytes=text.encode("utf-8")[:950_000], mime_type="text/plain"),
        )
        for name, text in documents.items()
    ]
    # Discovery Engine rejects FULL reconciliation for inline sources, so import
    # incrementally and delete stale documents explicitly. Otherwise an older
    # corpus keeps answering and every query falls back to local retrieval.
    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        inline_source=discoveryengine.ImportDocumentsRequest.InlineSource(documents=items),
        reconciliation_mode=discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
    )
    response = client.import_documents(request=request).result(timeout=300)
    errors = list(getattr(response, "error_samples", []) or [])
    if errors:
        raise RuntimeError(f"Agent Search import reported {len(errors)} error(s): {errors[0].message[:160]}")
    keep = {item.id for item in items}
    for existing in client.list_documents(parent=parent):
        if existing.id not in keep:
            client.delete_document(name=existing.name)
            log.info("Removed stale Agent Search document %s", existing.id)
    return len(items)


def reset_index_sync_state() -> None:
    global _INDEX_SYNCING
    with _INDEX_LOCK:
        _INDEX_SYNCING = False
