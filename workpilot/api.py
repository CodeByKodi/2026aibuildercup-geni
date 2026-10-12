"""WorkPilot JSON API: the React UI's backend, over the workflow session.

Every request loads the session from Firestore (`sessions/{id}`), applies one
workflow_session / workflow function, saves, and returns a view model built
from presentation helpers. Nothing here invents a status, a count or a metric:
each value comes from recorded state or a computed formula.

Human-only operations (approve, reject, execute, confirm an outcome) are
separate endpoints that a person clicks; the agent's tools cannot reach them.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import analytics, execution, presentation, scenario, state_store, workflow, workflow_session
from .ai import AIServiceError
from .documents import MIME_TYPES, load_sample_documents, source_id_for, word_count
from .config import CONFIG
from .impact import compute_impact, methodology_markdown
from .models import ActionStatus, ExecutionStatus, OutcomeStatus, utc_now
from .retrieval import SessionRetriever, chunk_sections

log = logging.getLogger("workpilot.api")

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "sample_data"
WEB_DIST = ROOT / "frontend" / "dist"

STAGE_LABELS = {
    "retrieving": "Searching enterprise sources",
    "reasoning": "Analysing evidence with Gemini",
    "structuring": "Recording the decision",
}
_TYPE_LABELS = {
    ".md": "Markdown", ".txt": "Text", ".csv": "CSV", ".pdf": "PDF", ".docx": "Word",
    ".png": "Image", ".jpg": "Image", ".jpeg": "Image", ".tif": "Image", ".tiff": "Image",
}
MAX_UPLOADS = 5

_documents: Dict[str, str] = {}
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# --------------------------------------------------------------------------
# Workspace documents
# --------------------------------------------------------------------------
def documents() -> Dict[str, str]:
    """The workspace documents, loaded once and registered as knowledge sources."""
    if not _documents:
        _documents.update(load_sample_documents(str(SAMPLE_DIR)))
        try:
            known = {s.get("name") for s in state_store.load_knowledge_sources()}
            missing = {k: v for k, v in _documents.items() if k not in known}
            if missing:
                workflow.register_sample_sources(missing)
        except Exception:
            log.exception("knowledge source registration failed")
        try:
            from .enterprise_search import configured as search_configured, ensure_index

            if search_configured():
                ensure_index(dict(_documents))
        except Exception:
            log.exception("search index sync failed")
    return _documents


def retriever(state: Dict[str, Any]) -> SessionRetriever:
    """Workspace search plus this session's uploads (searched locally, never added to the shared index)."""
    r = SessionRetriever(state.get("upload_texts") or {})
    r.build(state.get("documents") or documents())
    return r


def _lock(session_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
def load_state(session_id: str) -> Dict[str, Any]:
    stored = state_store.get_session(session_id)
    if not stored:
        raise HTTPException(404, f"Session {session_id} not found")
    state = workflow_session.fresh_state(session_id)
    current = state_store.load_actions()
    state["actions"] = current
    state.update(workflow_session.restore(stored, current))
    state["uploads"] = list(stored.get("uploads") or [])
    state["upload_texts"] = state_store.load_session_documents(session_id, state["uploads"]) if state["uploads"] else {}
    state["documents"] = {**documents(), **state["upload_texts"]}
    analytics.set_session(session_id)
    return state


def refresh_and_save(state: Dict[str, Any]) -> None:
    """After a human or agent step: re-read contracts from the store, then persist the session."""
    state["actions"] = state_store.load_actions()
    workflow_session.save(state)


def _trace_id(request: Optional[Request]) -> str:
    """Cloud Run's request trace id (X-Cloud-Trace-Context: TRACE_ID/SPAN;o=1), when present."""
    if request is None:
        return ""
    return (request.headers.get("x-cloud-trace-context") or "").split("/")[0][:64]


def _mark(state: Dict[str, Any], step: str, started: float, request: Optional[Request], **extra: Any) -> None:
    """Record when a step ran, how long it took on the server, and its trace id. Saved with the session."""
    proof = dict(state.get("proof") or {})
    proof[step] = {"at": utc_now(), "ms": int((time.perf_counter() - started) * 1000), "trace": _trace_id(request), **extra}
    state["proof"] = proof


def approver_name(state: Dict[str, Any]) -> str:
    persona = str(state.get("persona") or "")
    name = persona.removeprefix("You are ").split(",")[0].strip().rstrip(".")
    return f"Human reviewer as {name}" if name else "Human reviewer"


def _board_action(state: Dict[str, Any], action_id: str) -> Dict[str, Any]:
    """Only this session's contracts can be acted on from this session."""
    for action in workflow_session.session_board(state):
        if action.get("action_id") == action_id:
            return action
    raise HTTPException(404, f"Action {action_id} is not part of this session")


# --------------------------------------------------------------------------
# View models
# --------------------------------------------------------------------------
def _type_label(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return _TYPE_LABELS.get(suffix, suffix.lstrip(".").upper() or "File")


def source_cards(cited: Optional[set] = None, state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Real metadata for each document: type, size, words, chunks, the date it states, who added it."""
    docs = documents()
    registered: Dict[str, Dict[str, Any]] = {}
    try:
        registered = {str(s.get("name")): s for s in state_store.load_knowledge_sources()}
    except Exception:
        log.exception("knowledge source read failed")
    cards = []
    for name, text in docs.items():
        path = SAMPLE_DIR / name
        rec = registered.get(name) or {}
        as_of = presentation.evidence_as_of([name], docs)
        cards.append(
            {
                "name": name,
                "title": presentation.friendly_source_name(name),
                "type": _type_label(name),
                "bytes": path.stat().st_size if path.exists() else len(text.encode("utf-8")),
                "words": word_count(text),
                "chunks": len(chunk_sections(text)),  # the sections the search ranks
                "parser": str(rec.get("parser") or ""),
                "registered": bool(rec),
                "as_of": presentation.fmt_date(as_of) if as_of else "",
                "cited": bool(cited and name in cited),
                "uploaded": False,
            }
        )
    texts = (state or {}).get("upload_texts") or {}
    for up in (state or {}).get("uploads") or []:
        name = str(up.get("name") or "")
        cards.append(
            {
                "name": name,
                "source_id": up.get("source_id", ""),
                "title": presentation.friendly_source_name(name),
                "type": _type_label(name),
                "bytes": int(up.get("bytes") or 0),
                "words": int(up.get("words") or word_count(texts.get(name, ""))),
                "chunks": int(up.get("chunks") or 0),
                "parser": str(up.get("parser") or ""),
                "registered": name in texts,
                "as_of": "",
                "cited": bool(cited and name in cited),
                "uploaded": True,
                "truncated": bool(up.get("truncated")),
                "uploaded_at": up.get("uploaded_at", ""),
            }
        )
    return cards


def _receipts(board: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """The first successful Gmail/Calendar receipt per executed contract, from one read."""
    if not any(a.get("execution_status") == ExecutionStatus.EXECUTED for a in board):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for rec in state_store.load_executions(limit=200):
        if rec.status == ExecutionStatus.SUCCESS and rec.action_id not in out:
            out[rec.action_id] = {
                "kind": "Gmail draft" if rec.execution_type == execution.GMAIL_DRAFT else "Calendar event",
                "reference": rec.external_reference,
                "target": rec.target,
                "at": rec.created_at,
            }
    return out


def contract_view(action: Dict[str, Any], receipts: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    evidence = action.get("evidence") or ([action.get("source")] if action.get("source") else [])
    executed = action.get("execution_status") == ExecutionStatus.EXECUTED
    return {
        "action_id": action.get("action_id", ""),
        "title": action.get("title", ""),
        "owner": action.get("owner") or "Unassigned",
        "initials": presentation.initials(action.get("owner", "")),
        "due_date": action.get("due_date") or "TBD",
        "due_label": presentation.due_label(action.get("due_date")),
        "priority": action.get("priority") or "Medium",
        "status": ActionStatus.normalize(action.get("status")),
        "approval": presentation.approval_label(action.get("status", ActionStatus.PROPOSED)),
        "execution": presentation.execution_label(action),
        "expected_outcome": action.get("expected_outcome", ""),
        "rationale": action.get("rationale", ""),
        "evidence": [presentation.friendly_source_name(e) for e in evidence],
        "evidence_sources": [str(e) for e in evidence],
        "approved_by": action.get("approved_by", ""),
        "approved_at": action.get("approved_at", ""),
        "outcome_status": str(action.get("outcome_status") or OutcomeStatus.PENDING),
        "actual_outcome": action.get("actual_outcome", ""),
        "receipt": (receipts or {}).get(action.get("action_id", "")) if executed else None,
    }


def analysis_view(state: Dict[str, Any], board: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    analysis = state.get("last_analysis")
    if not analysis:
        return None
    decision = analysis.get("decision") or {}
    record = analysis.get("decision_record")
    sections = presentation.brief_sections(decision)
    grounded = workflow_session.has_grounded_analysis(state)
    return {
        "grounded": grounded,
        "decision_id": getattr(record, "decision_id", "") or state.get("session_decision_id", ""),
        "created_at": getattr(record, "created_at", ""),
        "ai_mode": analysis.get("ai_mode", ""),
        "retrieval_mode": presentation.retrieval_mode_label(analysis.get("retrieval_mode", "")),
        "duration_ms": int(analysis.get("duration_ms") or 0),
        "sections": sections,
        "stats": presentation.brief_stats(sections),
        "initial_verdict": presentation.verdict(decision, []) if grounded else None,
        "verdict": presentation.verdict(decision, board) if grounded else None,
        "evidence_map": [
            {**row, "title": presentation.friendly_source_name(row["source"])}
            for row in presentation.evidence_map(sections["blockers"])
        ],
        "no_answer": "" if grounded else (decision.get("finding") or decision.get("missing_information") or ""),
    }


_TIMED_STEPS = ("retrieve", "analyze", "contracts", "execute", "resume")


def impact_view(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    decision_id = state.get("session_decision_id") or ""
    if not decision_id:
        return None
    try:
        impact = compute_impact(decision_id=decision_id, documents=state.get("documents") or documents())
    except Exception:
        log.exception("impact computation failed")
        return None
    m = impact["metrics"]
    # Manual baseline (formula estimate) next to WorkPilot's own run time (measured on the server).
    proof = state.get("proof") or {}
    workpilot_ms = sum(int((proof.get(k) or {}).get("ms") or 0) for k in _TIMED_STEPS)
    baseline_min = float(m.get("estimated_total_time_saved_hours") or 0) * 60
    return {
        "baseline_minutes": round(baseline_min, 1),
        "workpilot_seconds": round(workpilot_ms / 1000, 1),
        "workpilot_steps": [k for k in _TIMED_STEPS if k in proof],
        "net_minutes": round(max(baseline_min - workpilot_ms / 60000, 0), 1),
        "hours": m.get("estimated_total_time_saved_hours"),
        "hours_label": presentation.hours_label(m.get("estimated_total_time_saved_hours")),
        "tiles": presentation.impact_tiles(m),
        "metrics": m,
        "assumptions": impact.get("assumptions"),
        "breakdown_minutes": impact.get("breakdown_minutes"),
        "per_task": impact.get("per_task") or [],
        "methodology": methodology_markdown(),
        "computed_at": impact.get("computed_at", ""),
    }


def agent_view(state: Dict[str, Any]) -> Dict[str, Any]:
    status = dict(workflow_session.agent_status(state))
    if status["mode"] == "ready" and workflow_session.agent_message_current(state):
        status["text"] = "Agent resumed and reassessed readiness · nothing is waiting on the agent"
    return status


def session_view(state: Dict[str, Any]) -> Dict[str, Any]:
    board = workflow_session.session_board(state)
    retrieved = state.get("retrieved") or {}
    hits = retrieved.get("hits") or []
    analysis = analysis_view(state, board)
    decision = (state.get("last_analysis") or {}).get("decision") or {}
    progress = presentation.board_progress(board)
    receipts = _receipts(board)
    message = state.get("agent_message")
    return {
        "session_id": state.get("ui_session_id", ""),
        "created_at": state.get("session_created_at", ""),
        "question": state.get("question", ""),
        "scenario_id": state.get("scenario_id", ""),
        "persona": state.get("persona", ""),
        "stage": workflow_session.stage(state),
        "flags": workflow_session.flags(state),
        "agent": agent_view(state),
        "agent_message": message,
        "agent_message_current": workflow_session.agent_message_current(state) if message else False,
        "agent_trace": list(state.get("agent_trace") or []),
        "retrieved": {
            "question": retrieved.get("question", ""),
            "mode": presentation.retrieval_mode_label(retrieved.get("mode", "")),
            "error": retrieved.get("error", ""),
            "not_found": retrieved.get("not_found", ""),
            "evidence": presentation.evidence_cards(hits, limit=8 + SessionRetriever.UPLOAD_HITS),
            "upload_passages": sum(1 for h in hits if h.get("source") in (state.get("upload_texts") or {})),
        }
        if retrieved
        else None,
        "sources": source_cards({h.get("source") for h in hits}, state),
        "analysis": analysis,
        "contracts": [contract_view(a, receipts) for a in board],
        "contracts_mode": state.get("contracts_mode", ""),
        "progress": progress,
        "tally": presentation.approval_tally(board),
        "outcomes": presentation.outcome_rows(board),
        "readiness": presentation.readiness_update(decision, board) if analysis and analysis["grounded"] else None,
        "followups_connected": presentation.followups_connected(),
        "bigquery_events": analytics.session_event_count(state.get("ui_session_id", "")) if analytics.configured() else None,
    }


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
app = FastAPI(title="WorkPilot AI API", docs_url="/api/docs", openapi_url="/api/openapi.json")


class NewSession(BaseModel):
    question: Optional[str] = None
    scenario_id: Optional[str] = None


class Retrieve(BaseModel):
    question: Optional[str] = None
    scenario_id: Optional[str] = None


class Execute(BaseModel):
    type: str


class Fields(BaseModel):
    owner: Optional[str] = None
    due_date: Optional[str] = None


class Outcome(BaseModel):
    actual_outcome: str
    outcome_status: Optional[str] = None


@app.exception_handler(AIServiceError)
def _ai_error(_, exc: AIServiceError):
    return JSONResponse({"detail": f"AI service error: {exc}"}, status_code=502)


@app.get("/api/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> Dict[str, Any]:
    return {
        "default_question": workflow.DEFAULT_QUESTION,
        "persona": scenario.PERSONA_LINE,
        "scenario_title": scenario.SCENARIO_TITLE,
        "release": scenario.RELEASE_NAME,
        "target_go_live": scenario.TARGET_GO_LIVE,
        "scenarios": [dict(s) for s in scenario.SCENARIOS],
        "sources": source_cards(),
        "outcome_statuses": list(OutcomeStatus.ALL),
        "followups_connected": presentation.followups_connected(),
        "challenge": presentation.CHALLENGE_NAME,
    }


def _scenario(scenario_id: Optional[str]) -> Optional[Dict[str, Any]]:
    return next((s for s in scenario.SCENARIOS if s["id"] == scenario_id), None)


def _apply_question(state: Dict[str, Any], question: Optional[str], scenario_id: Optional[str]) -> None:
    item = _scenario(scenario_id)
    if item:
        state["scenario_id"] = item["id"]
        state["persona"] = f"You are {item['persona']}."
    if question and question.strip():
        state["question"] = question.strip()
    elif item:
        state["question"] = item["question"]


@app.post("/api/sessions")
def create_session(body: NewSession) -> Dict[str, Any]:
    state = workflow_session.fresh_state()
    state["actions"] = []
    state.update(uploads=[], upload_texts={}, documents=documents())
    _apply_question(state, body.question, body.scenario_id)
    if not workflow_session.save(state):
        raise HTTPException(503, "The session could not be saved")
    return session_view(state)


@app.get("/api/sessions")
def list_sessions(ids: str = "") -> List[Dict[str, Any]]:
    """Summaries of the sessions this browser started (ids it remembers). Never the whole workspace."""
    out = []
    for sid in [i for i in ids.split(",") if i.strip()][:20]:
        stored = state_store.get_session(sid.strip())
        if not stored:
            continue
        out.append(
            {
                "session_id": stored.get("session_id", ""),
                "question": stored.get("question", ""),
                "stage": stored.get("stage", ""),
                "readiness": stored.get("readiness", ""),
                "decision_id": stored.get("decision_id", ""),
                "contracts": len(stored.get("action_contracts") or []),
                "created_at": stored.get("created_at", ""),
                "updated_at": stored.get("updated_at", ""),
            }
        )
    return out


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> Dict[str, Any]:
    return session_view(load_state(session_id))


@app.get("/api/sessions/{session_id}/impact")
def impact(session_id: str) -> Dict[str, Any]:
    """Computed on request (it reads the decision's whole record), shown on Outcome & Impact."""
    return {"impact": impact_view(load_state(session_id))}


@app.post("/api/sessions/{session_id}/retrieve")
def retrieve(session_id: str, body: Retrieve, request: Request) -> Dict[str, Any]:
    with _lock(session_id):
        started = time.perf_counter()
        state = load_state(session_id)
        _apply_question(state, body.question, body.scenario_id)
        try:
            state.update(workflow_session.retrieve(state["question"] or workflow.DEFAULT_QUESTION, retriever(state)))
        except Exception as exc:
            log.exception("retrieval failed")
            raise HTTPException(502, f"Retrieval failed: {type(exc).__name__}: {exc}")
        retrieved = state.get("retrieved") or {}
        _mark(state, "retrieve", started, request, mode=retrieved.get("mode", ""), passages=len(retrieved.get("hits") or []))
        refresh_and_save(state)
        return session_view(state)


def _ndjson(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj, default=str) + "\n").encode("utf-8")


@app.post("/api/sessions/{session_id}/analyze")
def analyze(session_id: str, request: Request) -> StreamingResponse:
    """Decide: the agent's turn, streamed as NDJSON stage events with server-measured times."""
    state = load_state(session_id)
    events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    def progress(stage: str) -> None:
        events.put({"type": "stage", "stage": stage, "label": STAGE_LABELS.get(stage, stage), "at_ms": elapsed_ms()})

    def work() -> None:
        with _lock(session_id):
            try:
                current = load_state(session_id)
                question = current.get("question") or workflow.DEFAULT_QUESTION
                current.update(
                    workflow_session.generate_brief(
                        question, retriever(current), progress=progress, session_id=session_id, documents=current["documents"]
                    )
                )
                _mark(current, "analyze", started, request, mode=(current.get("last_analysis") or {}).get("ai_mode", ""))
                refresh_and_save(current)
                events.put({"type": "done", "at_ms": elapsed_ms(), "session": session_view(current)})
            except Exception as exc:
                log.exception("brief failed")
                events.put({"type": "error", "at_ms": elapsed_ms(), "detail": f"{type(exc).__name__}: {exc}"})

    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(work,), daemon=True, name="workpilot-brief").start()

    def stream() -> Iterator[bytes]:
        yield _ndjson({"type": "start", "at_ms": 0, "question": state.get("question", "")})
        while True:
            try:
                event = events.get(timeout=10)
            except queue.Empty:
                yield _ndjson({"type": "tick", "at_ms": elapsed_ms()})  # keeps proxies from idling out
                continue
            yield _ndjson(event)
            if event["type"] in {"done", "error"}:
                return

    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _upload_name(name: str) -> str:
    clean = Path(str(name or "")).name.strip()[:120]
    if not clean or Path(clean).suffix.lower() not in MIME_TYPES:
        raise HTTPException(400, f"Supported files: {', '.join(sorted(MIME_TYPES))}")
    if clean in documents():
        raise HTTPException(409, f"{clean} is already a workspace document")
    return clean


@app.post("/api/sessions/{session_id}/documents")
async def upload(session_id: str, name: str, request: Request) -> Dict[str, Any]:
    """Add a document to this session: parsed (Document AI OCR for PDFs and images when
    configured, local parsers otherwise) and saved in Firestore with the session."""
    clean = _upload_name(name)
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "The file is empty")

    def work() -> Dict[str, Any]:
        with _lock(session_id):
            state = load_state(session_id)
            if state.get("last_analysis"):
                raise HTTPException(409, "This session already has a decision; start a new analysis to add documents")
            if len(state["uploads"]) >= MAX_UPLOADS and clean not in state["upload_texts"]:
                raise HTTPException(409, f"Up to {MAX_UPLOADS} uploads per session")
            try:
                text, meta = workflow.ingest_upload(clean, raw)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            if not text.strip():
                raise HTTPException(422, f"No text could be read from {clean}")
            entry = state_store.save_session_document(session_id, source_id_for(clean), clean, text, {**meta, "bytes": len(raw)})
            fresh = load_state(session_id)
            return {**session_view(fresh), "uploaded": entry}

    return await run_in_threadpool(work)


@app.delete("/api/sessions/{session_id}/documents/{source_id}")
def remove_upload(session_id: str, source_id: str) -> Dict[str, Any]:
    with _lock(session_id):
        state = load_state(session_id)
        if state.get("last_analysis"):
            raise HTTPException(409, "This session already has a decision; its evidence stays as recorded")
        if not state_store.delete_session_document(session_id, source_id):
            raise HTTPException(404, "No such upload in this session")
        return session_view(load_state(session_id))


@app.post("/api/sessions/{session_id}/contracts")
def contracts(session_id: str, request: Request) -> Dict[str, Any]:
    with _lock(session_id):
        started = time.perf_counter()
        state = load_state(session_id)
        analysis = state.get("last_analysis") or {}
        record = analysis.get("decision_record")
        if not workflow_session.has_grounded_analysis(state) or record is None:
            raise HTTPException(409, "Generate a grounded decision first")
        if workflow_session.session_board(state):
            return session_view(state)  # already prepared: a second click must not add a second set
        state.update(
            workflow_session.prepare_contracts(
                retriever(state), record, analysis.get("decision") or {}, session_id=session_id,
                documents=state["documents"], trace=state.get("agent_trace"),
            )
        )
        _mark(state, "contracts", started, request, mode=state.get("contracts_mode", ""))
        refresh_and_save(state)
        return session_view(state)


def _result(state: Dict[str, Any], ok: bool, message: str) -> Dict[str, Any]:
    refresh_and_save(state)
    if not ok:
        raise HTTPException(409, message or "Not saved")
    return {**session_view(state), "message": message}


@app.post("/api/sessions/{session_id}/actions/{action_id}/approve")
def approve(session_id: str, action_id: str) -> Dict[str, Any]:
    with _lock(session_id):
        state = load_state(session_id)
        _board_action(state, action_id)
        out = workflow.approve_and_execute(action_id, None, approved_by=approver_name(state))
        return _result(state, bool(out.get("ok")), out.get("message", ""))


@app.post("/api/sessions/{session_id}/actions/{action_id}/reject")
def reject(session_id: str, action_id: str) -> Dict[str, Any]:
    with _lock(session_id):
        state = load_state(session_id)
        _board_action(state, action_id)
        out = workflow.reject_action(action_id, rejected_by=approver_name(state))
        return _result(state, bool(out.get("ok")), out.get("message", ""))


@app.post("/api/sessions/{session_id}/actions/{action_id}/fields")
def edit_fields(session_id: str, action_id: str, body: Fields) -> Dict[str, Any]:
    with _lock(session_id):
        state = load_state(session_id)
        action = _board_action(state, action_id)
        if ActionStatus.normalize(action.get("status")) != ActionStatus.PROPOSED:
            raise HTTPException(409, "Only a pending contract can be edited")
        ok, msg = workflow.update_action_fields(action_id, owner=body.owner, due_date=body.due_date)
        return _result(state, ok, msg)


@app.post("/api/sessions/{session_id}/actions/{action_id}/execute")
def execute(session_id: str, action_id: str, body: Execute) -> Dict[str, Any]:
    if body.type not in (execution.GMAIL_DRAFT, execution.CALENDAR_FOLLOWUP):
        raise HTTPException(400, "type must be gmail_draft or calendar_followup")
    with _lock(session_id):
        state = load_state(session_id)
        _board_action(state, action_id)
        out = workflow.execute_action(action_id, body.type)
        return _result(state, bool(out.get("ok")), out.get("message", ""))


@app.post("/api/sessions/{session_id}/execute-approved")
def execute_approved(session_id: str, body: Execute, request: Request) -> Dict[str, Any]:
    """A person runs every approved, not-yet-executed contract in one click. Each becomes a real
    Gmail draft (never sent) or Calendar event; EXECUTED only where Google returns an ID."""
    if body.type not in (execution.GMAIL_DRAFT, execution.CALENDAR_FOLLOWUP):
        raise HTTPException(400, "type must be gmail_draft or calendar_followup")
    with _lock(session_id):
        state = load_state(session_id)
        todo = [
            a for a in workflow_session.session_board(state)
            if presentation.approval_label(a.get("status", "")) == "Approved" and a.get("execution_status") != ExecutionStatus.EXECUTED
        ]
        if not todo:
            raise HTTPException(409, "No approved action is waiting to run")
        started = time.perf_counter()
        results = []
        for action in todo:
            out = workflow.execute_action(action["action_id"], body.type)
            results.append({"action_id": action["action_id"], "ok": bool(out.get("ok")), "message": out.get("message", "")})
        _mark(state, "execute", started, request, ran=sum(1 for r in results if r["ok"]))
        refresh_and_save(state)
        failed = [r for r in results if not r["ok"]]
        view = session_view(state)
        if failed and len(failed) == len(results):
            raise HTTPException(502, failed[0]["message"] or "Execution did not run")
        return {**view, "results": results}


@app.get("/api/sessions/{session_id}/audit")
def audit(session_id: str) -> Dict[str, Any]:
    """The Firestore audit events behind this session's decision and contracts, newest first."""
    state = load_state(session_id)
    decision_id = state.get("session_decision_id") or ""
    ids = {a.get("action_id") for a in workflow_session.session_board(state)}
    rows = []
    for e in state_store.load_events(limit=1000):
        if not decision_id or (e.get("decision_id") != decision_id and e.get("action_id") not in ids):
            continue
        payload = e.get("payload") or {}
        rows.append({
            "event_id": e.get("event_id", ""),
            "event_type": e.get("event_type", ""),
            "action_id": e.get("action_id", ""),
            "at": e.get("created_at", ""),
            "by": payload.get("approved_by") or payload.get("verified_by") or payload.get("rejected_by") or "",
            "detail": payload.get("reference") or payload.get("actual") or payload.get("status") or payload.get("title") or "",
            "outcome_status": payload.get("outcome_status", ""),
        })
    return {
        "events": rows[:60],
        "store": "Firestore" if state_store.using_firestore() else "in-memory (local)",
        # Dataset and table only: the project id stays out of public pages, as on System Status.
        "bigquery": {"configured": analytics.configured(), "table": f"{CONFIG.bigquery_dataset}.{CONFIG.bigquery_table}" if analytics.configured() else ""},
    }


@app.get("/api/sessions/{session_id}/actions/{action_id}/preview")
def preview(session_id: str, action_id: str) -> Dict[str, Any]:
    """Exactly what running this contract would create, built by the same code as execution.
    Nothing is sent to Google."""
    state = load_state(session_id)
    action = _board_action(state, action_id)
    decision = state_store.get_decision(action.get("decision_id", "")) if action.get("decision_id") else None
    subject, body = execution.compose_followup(action, decision)
    to = execution.owner_email(action.get("owner", ""))
    return {
        "gmail": {"to": to, "subject": subject, "body": body, "sent": False},
        "calendar": {"title": subject, "date": execution.parse_due_date(action.get("due_date", "")).isoformat(), "attendee": to, "description": body},
        "connected": presentation.followups_connected(),
        "approval": presentation.approval_label(action.get("status", "")),
    }


@app.get("/api/sessions/{session_id}/proof")
def proof(session_id: str) -> Dict[str, Any]:
    """What each Google Cloud service did for this session: resource, last call, trace id, ids, counts."""
    state = load_state(session_id)
    p = state.get("proof") or {}
    board = workflow_session.session_board(state)
    decision_id = state.get("session_decision_id") or ""
    events = []
    if decision_id:
        ids = {a.get("action_id") for a in board}
        events = [e for e in state_store.load_events(limit=1000) if e.get("decision_id") == decision_id or e.get("action_id") in ids]
    executed = [a for a in board if a.get("execution_status") == ExecutionStatus.EXECUTED]
    uploads = state.get("uploads") or []
    from . import adk_orchestrator, document_ai, enterprise_search

    # Name the backend that actually served each step; locally that is not Google.
    search_res = (f"engine {CONFIG.vertex_search_engine_id} · data store {CONFIG.vertex_search_data_store_id}"
                  if enterprise_search.configured() else "not configured: local TF-IDF search")
    model_res = (f"model {CONFIG.gemini_model} · agent WorkPilot Orchestrator" if adk_orchestrator.configured()
                 else f"model {CONFIG.gemini_model} (no ADK agent)" if not CONFIG.demo_mode else "not called: DEMO_MODE synthesis")
    store_res = f"workpilot_workspaces/{CONFIG.workspace}" if state_store.using_firestore() else "not configured: in-memory store"
    rows = [
        {"service": "Vertex AI Search", "resource": search_res,
         "used_for": "Knowledge search", "last": p.get("retrieve"),
         "detail": (p.get("retrieve") or {}).get("mode", "")},
        {"service": "Gemini on Vertex AI via ADK", "resource": model_res,
         "used_for": "Analyze, Action Contracts, resume", "last": p.get("resume") or p.get("contracts") or p.get("analyze"),
         "detail": (p.get("analyze") or {}).get("mode", "")},
        {"service": "Document AI", "resource": "OCR processor" if document_ai.configured() else "not configured: local parsers",
         "used_for": "Uploaded scans", "last": {"at": uploads[-1].get("uploaded_at"), "ms": uploads[-1].get("duration_ms")} if uploads else None,
         "detail": ", ".join(f"{u.get('name')} ({u.get('parser')})" for u in uploads)},
        {"service": "Firestore", "resource": store_res,
         "used_for": "Session, decision, actions, audit", "last": {"at": (state_store.get_session(session_id) or {}).get("updated_at")},
         "detail": f"session {session_id}" + (f" · decision {decision_id}" if decision_id else "") + f" · {len(events)} audit events"},
        {"service": "BigQuery", "resource": f"{CONFIG.bigquery_dataset}.{CONFIG.bigquery_table}" if analytics.configured() else "not configured",
         "used_for": "Event analytics", "last": None,
         "detail": "", "count": analytics.decision_event_count(decision_id)},
        {"service": "Gmail / Calendar", "resource": "drafts and events via user OAuth" if presentation.followups_connected() else "not connected",
         "used_for": "Approved actions only", "last": p.get("execute"),
         "detail": ", ".join(f"{a.get('action_id')}" for a in executed)},
        {"service": "Cloud Run", "resource": f"service {os.getenv('K_SERVICE', 'local')} · revision {os.getenv('K_REVISION', '—')}",
         "used_for": "Runs the UI, API and agent", "last": None, "detail": os.getenv("CLOUD_RUN_REGION", "")},
    ]
    return {
        "rows": rows,
        "ids": {"session": session_id, "decision": decision_id, "actions": [a.get("action_id") for a in board]},
        "events": len(events),
        # Executed work with no recorded approver. execute_followup refuses that, so this should read 0.
        "executed": len(executed),
        "unapproved_executions": sum(1 for a in executed if not a.get("approved_by")),
    }


@app.post("/api/sessions/{session_id}/actions/{action_id}/outcome/suggest")
def suggest(session_id: str, action_id: str, body: Outcome) -> Dict[str, Any]:
    """The agent proposes an outcome status. Nothing is recorded: a person confirms."""
    state = load_state(session_id)
    action = _board_action(state, action_id)
    if not body.actual_outcome.strip():
        raise HTTPException(400, "Describe the actual outcome first")
    s = workflow_session.suggest_outcome(action, body.actual_outcome, retriever(state), session_id, state["documents"])
    return {
        "suggested_status": s.get("suggested_status", ""),
        "explanation": s.get("summary") or s.get("reasoning", ""),
        "mode": s.get("mode", ""),
        "agent_trace": s.get("agent_trace") or [],
    }


@app.post("/api/sessions/{session_id}/actions/{action_id}/outcome/confirm")
def confirm(session_id: str, action_id: str, body: Outcome) -> Dict[str, Any]:
    if body.outcome_status not in OutcomeStatus.ALL or body.outcome_status == OutcomeStatus.PENDING:
        raise HTTPException(400, "Choose an outcome status")
    if not body.actual_outcome.strip():
        raise HTTPException(400, "Describe the actual outcome first")
    with _lock(session_id):
        state = load_state(session_id)
        _board_action(state, action_id)
        ok, msg = workflow.verify_outcome(action_id, body.actual_outcome.strip(), body.outcome_status, verified_by=approver_name(state))
        return _result(state, ok, msg)


@app.post("/api/sessions/{session_id}/resume")
def resume(session_id: str, request: Request) -> Dict[str, Any]:
    with _lock(session_id):
        started = time.perf_counter()
        state = load_state(session_id)
        if not workflow_session.agent_status(state)["can_resume"]:
            raise HTTPException(409, "The agent is waiting for a person to approve or reject every Action Contract")
        state.update(workflow_session.resume_agent(state, retriever(state)))
        _mark(state, "resume", started, request, fallback=bool((state.get("agent_message") or {}).get("fallback")))
        refresh_and_save(state)
        return session_view(state)


@app.get("/api/memory")
def memory(ids: str = "") -> List[Dict[str, Any]]:
    """Decision Memory for the decisions this browser's sessions recorded."""
    wanted = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))[:20]
    receipts = _receipts([{"execution_status": ExecutionStatus.EXECUTED}])
    out = []
    for decision_id in wanted:
        # Looked up by id: the shared workspace holds more decisions than one listing returns.
        rec = state_store.get_decision(decision_id)
        if rec is None:
            continue
        actions = state_store.actions_for_decision(decision_id)
        approvers = sorted({a.get("approved_by") for a in actions if a.get("approved_by")})
        entry = {"actions": actions, "approved_by": ", ".join(approvers) or rec.approved_by or "Pending"}
        out.append(
            {
                "decision_id": rec.decision_id,
                "question": rec.question,
                "finding": rec.finding,
                "recommendation": rec.recommendation,
                "rationale": rec.rationale,
                "status": rec.status,
                "readiness_status": rec.readiness_status,
                "expected_outcome": rec.expected_outcome,
                "actual_outcome": rec.actual_outcome,
                "approved_by": entry["approved_by"],
                "created_at": rec.created_at,
                "evidence": [{"source": presentation.friendly_source_name(e.get("source", "")), "excerpt": presentation.snippet(presentation.clean_excerpt(e.get("excerpt", "")), 220)} for e in rec.evidence[:6]],
                "actions": [contract_view(a, receipts) for a in entry["actions"]],
            }
        )
    return out


@app.get("/api/status")
def status() -> Dict[str, Any]:
    from .cloud_status import technology_status

    return {"rows": presentation.public_stack_rows(technology_status()), "checked_at": utc_now()}


# --------------------------------------------------------------------------
# The React app (built into frontend/dist). Unknown paths fall back to index.html.
# --------------------------------------------------------------------------
@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    target = (WEB_DIST / path).resolve()
    if path and target.is_file() and WEB_DIST.resolve() in target.parents:
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
        return FileResponse(target, headers=headers)
    index = WEB_DIST / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "The web UI is not built. Run `npm run build` in frontend/."}, status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-store"})
