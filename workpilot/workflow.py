"""WorkPilot workflow: Know → Decide → Approve → Act → Measure.

UI-independent orchestration of the use cases so the logic is testable and
the UI stays thin. Every step writes Firestore state and an audit event, and
mirrors the event to BigQuery on a best-effort basis.
"""
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import ai, analytics, execution, scenario, state_store
from .config import CONFIG, IMPACT
from .documents import chunks_to_text, process_document, source_id_for, word_count
from .models import ActionStatus, DecisionRecord, ExecutionStatus, SourceChunk, WorkAction, make_decision_id
from .retrieval import EnterpriseRetriever

log = logging.getLogger("workpilot.workflow")

DEFAULT_QUESTION = scenario.QUESTION


def new_session_id() -> str:
    return "SES-" + uuid.uuid4().hex[:12]


def _emit(event_type: str, payload: Dict[str, Any], **kwargs: Any) -> None:
    """Firestore audit event + BigQuery analytics event (best effort)."""
    try:
        state_store.log_event(
            event_type,
            payload,
            decision_id=kwargs.get("decision_id", ""),
            action_id=kwargs.get("action_id", ""),
            estimated_minutes_saved=kwargs.get("estimated_minutes_saved", 0.0),
        )
    except Exception as exc:  # Firestore outage must not kill the flow
        log.warning("audit event not stored (%s): %s", event_type, exc)
    analytics.log_metric(event_type, payload, **kwargs)


# --------------------------------------------------------------------------
# Know
# --------------------------------------------------------------------------
def register_source(name: str, text: str, parser: str, chunk_count: int = 1) -> Dict[str, Any]:
    words = word_count(text)
    sid = source_id_for(name)
    state_store.record_knowledge_source(sid, name, words, parser, chunk_count)
    minutes = words / IMPACT.reading_words_per_minute if IMPACT.reading_words_per_minute else 0.0
    _emit(
        "document_processed",
        {"source": name, "parser": parser, "word_count": words, "chunks": chunk_count},
        source_count=1,
        estimated_minutes_saved=minutes,
    )
    return {"source_id": sid, "name": name, "word_count": words, "parser": parser, "chunks": chunk_count}


def ingest_upload(name: str, raw: bytes) -> Tuple[str, Dict[str, Any]]:
    started = time.perf_counter()
    chunks, parser = process_document(name, raw)
    text = chunks_to_text(chunks)
    meta = register_source(name, text, parser, len(chunks))
    meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return text, meta


def register_sample_sources(documents: Dict[str, str]) -> None:
    for name, text in documents.items():
        register_source(name, text, "Sample workspace document")


# --------------------------------------------------------------------------
# Decide
# --------------------------------------------------------------------------
BRIEF_STAGES = ("retrieving", "reasoning", "structuring")


def run_analysis(
    question: str,
    retriever: EnterpriseRetriever,
    k: int = 6,
    progress=None,
) -> Dict[str, Any]:
    """Retrieve → reason → structure. `progress` receives each stage name in BRIEF_STAGES."""
    started = time.perf_counter()

    def note(step: str) -> None:
        if progress:
            progress(step)

    note("retrieving")
    hits: List[SourceChunk] = retriever.search(question, k=k)
    retrieval_mode = retriever.last_mode
    _emit("search_performed", {"question": question, "retrieval": retrieval_mode, "hits": len(hits)}, source_count=len({h.source for h in hits}))

    if not hits:
        # Nothing to ground on: no Gemini call, no DecisionRecord, no blockers.
        reason = getattr(retriever, "not_found", "") or ai.NOT_FOUND
        return {
            "decision_record": None,
            "decision": ai.not_found_decision(reason),
            "markdown": reason,
            "hits": [],
            "ai_mode": ai.MODE_NO_EVIDENCE,
            "retrieval_mode": retrieval_mode,
            "retrieval_error": getattr(retriever, "last_error", ""),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    note("reasoning")
    decision, ai_mode = ai.analyze_decision(question, hits)
    note("structuring")
    duration_ms = int((time.perf_counter() - started) * 1000)
    if decision.get("answer_found") is False:
        return {
            "decision_record": None,
            "decision": decision,
            "markdown": decision.get("finding", ""),
            "hits": hits,
            "ai_mode": ai_mode,
            "retrieval_mode": retrieval_mode,
            "retrieval_error": getattr(retriever, "last_error", ""),
            "duration_ms": duration_ms,
        }

    record = record_decision(question, decision, hits, ai_mode, retrieval_mode, duration_ms)

    return {
        "decision_record": record,
        "decision": decision,
        "markdown": ai.decision_to_markdown(decision),
        "hits": hits,
        "ai_mode": ai_mode,
        "retrieval_mode": retrieval_mode,
        "retrieval_error": getattr(retriever, "last_error", ""),
        "duration_ms": duration_ms,
    }


def record_decision(
    question: str,
    decision: Dict[str, Any],
    hits: List[SourceChunk],
    ai_mode: str,
    retrieval_mode: str,
    duration_ms: int = 0,
    session_id: str = "",
) -> DecisionRecord:
    """Persist a DecisionRecord grounded in `hits` and emit its analytics events.

    Shared by the deterministic pipeline and the agent's Decision Tool, so both
    paths write the same record and the same events.
    """
    record = DecisionRecord(
        decision_id=make_decision_id(),
        workspace_id=CONFIG.workspace,
        session_id=session_id,
        question=question,
        finding=decision.get("finding", ""),
        recommendation=decision.get("recommendation", ""),
        rationale=decision.get("rationale", ""),
        evidence=[{"source": h.source, "excerpt": h.text[:600], "page": getattr(h, "page", 1)} for h in hits],
        expected_outcome=decision.get("expected_outcome", ""),
        readiness_status=decision.get("readiness_status", ""),
        approval_conditions=decision.get("approval_conditions") or [],
        ai_mode=ai_mode,
        retrieval_mode=retrieval_mode,
    )
    state_store.save_decision(record)

    sources = sorted({h.source for h in hits})
    _emit(
        "knowledge_analyzed",
        {"question": question, "sources": sources, "ai": ai_mode, "retrieval": retrieval_mode},
        decision_id=record.decision_id,
        source_count=len(sources),
        duration_ms=duration_ms,
    )
    _emit(
        "decision_supported",
        {"finding": record.finding[:300], "blockers": len(decision.get("blockers") or [])},
        decision_id=record.decision_id,
        estimated_minutes_saved=IMPACT.minutes_per_decision,
    )
    if decision.get("blockers"):
        count = len(decision["blockers"])
        _emit("action_identified", {"count": count, "origin": "decision_analysis"}, decision_id=record.decision_id)
        _emit("blocker_identified", {"count": count, "origin": "decision_analysis"}, decision_id=record.decision_id)
        _emit("decision_created", {"decision_id": record.decision_id, "readiness": decision.get("readiness_status", "")}, decision_id=record.decision_id)
    return record


# --------------------------------------------------------------------------
# Action Contracts
# --------------------------------------------------------------------------
def generate_contracts(
    retriever: EnterpriseRetriever,
    decision_record: Optional[DecisionRecord],
    decision: Optional[Dict[str, Any]] = None,
    k: int = 10,
) -> Tuple[List[Dict[str, Any]], str]:
    started = time.perf_counter()
    query = decision_record.question if decision_record else DEFAULT_QUESTION
    hits = retriever.search(f"{query} blockers actions owners deadlines priorities expected outcomes", k=k)
    decision_id = decision_record.decision_id if decision_record else ""
    actions, ai_mode = ai.extract_actions(hits, decision_id=decision_id, decision=decision)
    return _persist_contracts(actions, decision_id, ai_mode, retriever.last_mode, started), ai_mode


def propose_contracts(
    actions: List[WorkAction], decision_id: str, origin: str, retrieval_mode: str, started: float
) -> List[Dict[str, Any]]:
    """Persist proposed Action Contracts for a decision. They start PROPOSED; only a person approves.

    `started` is a time.perf_counter() value from when the work began, so analytics record its real duration.
    """
    return _persist_contracts(actions, decision_id, origin, retrieval_mode, started)


def _persist_contracts(actions: List[WorkAction], decision_id: str, ai_mode: str, retrieval_mode: str, started: float) -> List[Dict[str, Any]]:
    dicts = [a.to_dict() for a in actions if not scenario.is_human_gate_action(a.to_dict())]
    persisted = state_store.save_actions(dicts)
    if decision_id:
        state_store.link_actions_to_decision(decision_id, [d["action_id"] for d in dicts])
    duration_ms = int((time.perf_counter() - started) * 1000)
    for d in dicts:
        _emit(
            "action_contract_created",
            {"title": d["title"], "owner": d["owner"], "priority": d["priority"], "ai": ai_mode},
            decision_id=decision_id,
            action_id=d["action_id"],
            estimated_minutes_saved=IMPACT.minutes_per_prepared_action,
        )
    _emit(
        "action_plan_generated",
        {"action_count": len(dicts), "persisted_count": persisted, "ai": ai_mode, "retrieval": retrieval_mode},
        decision_id=decision_id,
        duration_ms=duration_ms,
    )
    return state_store.load_actions()


# --------------------------------------------------------------------------
# Meeting-to-Action
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Approve → Act
# --------------------------------------------------------------------------
def reject_action(action_id: str, rejected_by: str = "Human reviewer") -> Dict[str, Any]:
    """Record an explicit human rejection. Does not execute anything."""
    ok, message = state_store.reject_action(action_id, rejected_by=rejected_by)
    action = state_store.get_action(action_id)
    if ok and action:
        _emit(
            "action_rejected",
            {"title": action.get("title", ""), "rejected_by": rejected_by},
            decision_id=action.get("decision_id", ""),
            action_id=action_id,
        )
    return {"ok": ok, "message": message, "action": action}


def approve_and_execute(action_id: str, execution_type: Optional[str], approved_by: str = "Human reviewer") -> Dict[str, Any]:
    ok, message = state_store.approve_action(action_id, approved_by=approved_by)
    action = state_store.get_action(action_id)
    if action is None:
        return {"ok": False, "message": message, "action": None, "execution": None}
    if ok:
        _emit(
            "action_approved",
            {"title": action["title"], "owner": action["owner"], "priority": action["priority"], "approved_by": approved_by},
            decision_id=action.get("decision_id", ""),
            action_id=action_id,
        )
    elif action["status"] in {ActionStatus.PROPOSED, ActionStatus.REJECTED}:
        return {"ok": False, "message": message, "action": action, "execution": None}

    record = None
    if execution_type:
        result = execute_action(action_id, execution_type)
        record = result["execution"]
        action = result["action"] or action
    return {"ok": True, "message": message, "action": action, "execution": record}


def execute_action(action_id: str, execution_type: str) -> Dict[str, Any]:
    """Run the approved follow-up through the live adapter (explicit user click).

    The action becomes EXECUTED only when the Gmail/Calendar API returns an ID.
    Without OAuth it stays Not started and the receipt says why.
    """
    action = state_store.get_action(action_id)
    if action is None:
        return {"ok": False, "message": f"Action {action_id} not found", "action": None, "execution": None}
    decision = state_store.get_decision(action.get("decision_id", "")) if action.get("decision_id") else None
    try:
        record = execution.execute_followup(action, execution_type, decision)
    except (execution.ApprovalRequired, ValueError) as exc:
        return {"ok": False, "message": str(exc), "action": action, "execution": None}
    if record.status == ExecutionStatus.SUCCESS:
        event = {
            execution.GMAIL_DRAFT: "gmail_draft_created",
            execution.CALENDAR_FOLLOWUP: "calendar_followup_created",
        }[execution_type]
        _emit(
            event,
            {"mode": record.mode, "target": record.target, "reference": record.external_reference},
            decision_id=record.decision_id,
            action_id=action_id,
            estimated_minutes_saved=IMPACT.minutes_per_followup,
        )
        message = f"Executed · {record.external_reference}"
    elif record.status == ExecutionStatus.UNAVAILABLE:
        _emit("execution_unavailable", {"type": execution_type, "reason": record.error}, decision_id=record.decision_id, action_id=action_id)
        message = record.error or execution.NOT_CONNECTED
    else:
        _emit("execution_failed", {"type": execution_type, "mode": record.mode, "error": record.error}, decision_id=record.decision_id, action_id=action_id)
        message = f"Execution failed: {record.error}"
    return {
        "ok": record.status == ExecutionStatus.SUCCESS,
        "message": message,
        "action": state_store.get_action(action_id),
        "execution": record,
    }


# --------------------------------------------------------------------------
# Measure
# --------------------------------------------------------------------------
def verify_outcome(action_id: str, actual_outcome: str, outcome_status: str, verified_by: str = "Human reviewer") -> Tuple[bool, str]:
    ok, message = state_store.record_outcome(action_id, actual_outcome, outcome_status)
    if not ok:
        return ok, message
    action = state_store.get_action(action_id) or {}
    _emit(
        "outcome_verified",
        {"title": action.get("title", ""), "expected": action.get("expected_outcome", ""), "actual": actual_outcome, "outcome_status": outcome_status, "verified_by": verified_by},
        decision_id=action.get("decision_id", ""),
        action_id=action_id,
    )
    decision_id = action.get("decision_id")
    if decision_id:
        siblings = state_store.actions_for_decision(decision_id)
        verified = [a for a in siblings if a.get("outcome_status") != "PENDING"]
        summary = "; ".join(f"{a['title']}: {a['outcome_status']}" for a in verified)
        all_done = siblings and len(verified) == len(siblings)
        state_store.update_decision(decision_id, actual_outcome=summary, status="CLOSED" if all_done else "ACTIONED")
        ready = scenario.readiness_from_actions(siblings)
        _emit(
            "release_readiness_updated",
            {"status": ready["status"], "remaining": ready["remaining"], "verified_by": verified_by},
            decision_id=decision_id,
        )
    return ok, message


def update_action_fields(action_id: str, **fields: Any) -> Tuple[bool, str]:
    current = state_store.get_action(action_id)
    if current is None:
        return False, f"Action {action_id} not found"
    allowed = {"title", "owner", "due_date", "priority", "expected_outcome", "rationale"}
    current.update({k: v for k, v in fields.items() if k in allowed and v is not None})
    state_store.save_action(current)
    return True, "updated"


