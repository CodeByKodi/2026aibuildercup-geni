"""Workflow session: the business state of one WorkPilot run.

The API keeps the state as a plain mapping, loaded from Firestore per request. Every
function here takes that state as a plain mapping and returns the updates to
apply, so the workflow can be persisted, restored and driven by something
other than the UI (the WorkPilot agent, tests).

Firestore holds the durable copy in `sessions/{session_id}`; nothing critical
lives only in browser memory.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Mapping, Optional

from . import presentation, scenario, state_store, workflow
from .models import DecisionRecord, utc_now
from .retrieval import EnterpriseRetriever

log = logging.getLogger("workpilot.session")

STEPS = presentation.STEPS


def fresh_state(session_id: str = "") -> Dict[str, Any]:
    """State for a brand-new analysis."""
    return {
        "ui_session_id": session_id or workflow.new_session_id(),
        "step": "Know",
        "view": "workflow",
        "last_analysis": None,
        "retrieved": None,
        "session_decision_id": "",
        "session_action_ids": [],
        "contracts_for": "",
        "brief_error": "",
        "retrieve_error": "",
        "question": workflow.DEFAULT_QUESTION,
        "persona": scenario.PERSONA_LINE,
        "scenario_id": "go-live",
        "agent_trace": [],
        "agent_message": None,
        "session_created_at": utc_now(),
    }


# --------------------------------------------------------------------------
# Derived views of the state
# --------------------------------------------------------------------------
def session_board(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """This session's Action Contracts, never the whole shared workspace."""
    return [
        action
        for action in presentation.session_actions(
            state.get("actions") or [],
            state.get("session_decision_id") or "",
            state.get("session_action_ids") or [],
        )
        if not scenario.is_human_gate_action(action)
    ]


def has_grounded_analysis(state: Mapping[str, Any]) -> bool:
    """A brief that answered the question. "Not found" does not complete Decide."""
    analysis = state.get("last_analysis")
    return bool(analysis) and (analysis.get("decision") or {}).get("answer_found") is not False


def flags(state: Mapping[str, Any]) -> Dict[str, bool]:
    retrieved = state.get("retrieved") or {}
    return presentation.flags_from_board(
        has_retrieval=bool(retrieved.get("hits")),
        has_analysis=has_grounded_analysis(state),
        actions=session_board(state),
    )


# Agent workflow stages, in order. The current stage is the work still to do.
STAGES = ("KNOWLEDGE", "ANALYZE", "ACTIONS", "APPROVAL", "OUTCOME", "IMPACT")

_NEXT_STEP = {
    "KNOWLEDGE": "Search the workspace for evidence that answers the question.",
    "ANALYZE": "Reason over the evidence and record a decision.",
    "ACTIONS": "Propose Action Contracts for the decision's blockers.",
    "APPROVAL": "Wait: a person must approve or reject each proposed contract.",
    "OUTCOME": "Wait for approved work to run, then evaluate outcomes for a person to confirm.",
    "IMPACT": "Reassess readiness and calculate the impact.",
}


def stage(state: Mapping[str, Any]) -> str:
    """Where this session's workflow stands, derived from recorded state only."""
    retrieved = state.get("retrieved") or {}
    if not retrieved.get("hits"):
        return "KNOWLEDGE"
    if not has_grounded_analysis(state):
        return "ANALYZE"
    board = session_board(state)
    if not board:
        return "ACTIONS"
    progress = presentation.board_progress(board)
    if progress["pending"]:
        return "APPROVAL"
    if progress["measurable"] and progress["measured"] < progress["measurable"]:
        return "OUTCOME"
    return "IMPACT"


def next_step(current: str) -> str:
    return _NEXT_STEP.get(current, "")


def agent_status(state: Mapping[str, Any]) -> Dict[str, Any]:
    """What the agent is doing, read from the saved workflow: working, paused at the human gate, or resumable.

    `can_resume` is true once every contract has a human decision: the agent may pick the workflow
    back up from Firestore, but nothing it does approves, executes or confirms.
    """
    current = stage(state)
    board = session_board(state)
    p = presentation.board_progress(board)
    can_resume = p["total"] > 0 and p["pending"] == 0
    if current == "APPROVAL":
        mode, text = "paused", f"Agent paused · waiting for a person to approve {p['pending']} of {p['total']} Action Contracts"
    elif current == "OUTCOME" and p["executed"] < p["approved"]:
        mode, text = "paused", f"Agent paused · {p['approved'] - p['executed']} approved action(s) waiting to be executed by a person on Approval"
    elif current == "OUTCOME":
        open_n = p["measurable"] - p["measured"]
        mode, text = "paused", f"Agent paused · {open_n} outcome(s) waiting for a person to confirm on Outcome & Impact"
    elif current == "IMPACT":
        mode, text = "ready", "All outcomes recorded · resume the agent to reassess readiness and impact"
    else:
        mode, text = "working", f"Agent next: {next_step(current)}"
    return {"stage": current, "mode": mode, "text": text, "can_resume": can_resume}


def board_fingerprint(state: Mapping[str, Any]) -> List[int]:
    """What the human side of the workflow looked like: approved, rejected, executed, measured."""
    p = presentation.board_progress(session_board(state))
    return [p["approved"], p["rejected"], p["executed"], p["measured"]]


def agent_message_current(state: Mapping[str, Any]) -> bool:
    """False once people have acted since the agent last resumed: its message is then out of date."""
    message = state.get("agent_message") or {}
    return bool(message.get("ok")) and message.get("board") == board_fingerprint(state)


def resume_agent(state: Mapping[str, Any], retriever: EnterpriseRetriever) -> Dict[str, Any]:
    """The agent resumes after the human gate. Returns state updates; the message is saved with the session."""
    from .agent import orchestrator

    decision_id = state.get("session_decision_id") or ""
    result = orchestrator.run_resume(
        decision_id, retriever, dict(state.get("documents") or {}), state.get("ui_session_id") or ""
    )
    if result is None:
        return _resume_without_agent(state, decision_id)
    steps = agent_trace(result["agent_steps"])
    return {
        "agent_message": {
            "ok": True,
            "text": result["summary"],
            "readiness": result["readiness"].get("status", ""),
            "headline": result["readiness"].get("headline", ""),
            "impact_hours": result["impact_hours"],
            "steps": steps,
            "at": utc_now(),
            "board": board_fingerprint(state),
        },
        "agent_trace": list(state.get("agent_trace") or []) + steps,
    }


def _resume_without_agent(state: Mapping[str, Any], decision_id: str) -> Dict[str, Any]:
    """When the agent's turn does not complete, re-read the saved workflow and recompute readiness
    and impact directly, with the same functions the agent's tools call. Labelled as such."""
    import time

    from .impact import compute_impact

    steps: List[str] = []
    t0 = time.perf_counter()
    record = state_store.get_decision(decision_id) if decision_id else None
    actions = state_store.actions_for_decision(decision_id) if decision_id else []
    steps.append(f"read workflow from Firestore · {time.perf_counter() - t0:.1f} s")
    t1 = time.perf_counter()
    decision = dict((state.get("last_analysis") or {}).get("decision") or {})
    if record is not None and record.approval_conditions:
        decision["approval_conditions"] = record.approval_conditions
    verdict = presentation.verdict(decision, actions) or {"status": "NOT READY", "headline": "NOT READY"}
    steps.append(f"assess readiness · {time.perf_counter() - t1:.1f} s")
    t2 = time.perf_counter()
    try:
        hours = compute_impact(decision_id=decision_id, documents=dict(state.get("documents") or {}))["metrics"].get(
            "estimated_total_time_saved_hours"
        )
    except Exception:
        log.exception("impact during resume failed")
        hours = None
    steps.append(f"calculate impact · {time.perf_counter() - t2:.1f} s")
    p = presentation.board_progress([a for a in actions if not scenario.is_human_gate_action(a)])
    done = f"{p['approved']} approved, {p['executed']} executed, {p['measured']} outcome(s) confirmed"
    tail = (
        "All approval conditions are satisfied; the release is ready for approval by a person."
        if verdict["status"] == "READY FOR APPROVAL"
        else f"{verdict['headline']}."
    )
    estimate = f" Estimated time saved: {presentation.hours_label(hours)} (formula estimate)." if hours is not None else ""
    return {
        "agent_message": {
            "ok": True,
            "fallback": True,
            "text": f"Gemini did not complete the agent turn, so WorkPilot re-read the saved workflow itself: {done}. {tail}{estimate}",
            "readiness": verdict["status"],
            "headline": verdict["headline"],
            "impact_hours": hours,
            "steps": steps,
            "at": utc_now(),
            "board": board_fingerprint(state),
        },
        "agent_trace": list(state.get("agent_trace") or []) + [f"resume without agent · {s}" for s in steps],
    }


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def session_payload(state: Mapping[str, Any]) -> Dict[str, Any]:
    """The Firestore session document for this state."""
    contracts = session_board(state)
    done = flags(state)
    return {
        "session_id": state.get("ui_session_id") or "",
        "current_step": state.get("step", "Know"),
        "view": "workflow",
        "question": state.get("question", workflow.DEFAULT_QUESTION),
        "decision_id": state.get("session_decision_id", ""),
        "selected_action_ids": [a.get("action_id") for a in contracts if a.get("action_id")]
        or list(state.get("session_action_ids") or []),
        "action_contracts": contracts,
        "retrieved": state.get("retrieved"),
        "analysis": scenario.snapshot_analysis(state.get("last_analysis")),
        "contracts_for": state.get("contracts_for", ""),
        "approval_state": "complete" if done.get("Approve") else "pending",
        "execution_state": "executed" if done.get("Act") else "not_started",
        "outcome_state": "measured" if done.get("Measure") else "pending",
        "scenario_id": state.get("scenario_id") or "go-live",
        "persona": state.get("persona") or scenario.PERSONA_LINE,
        "agent_trace": list(state.get("agent_trace") or []),
        "agent_message": state.get("agent_message"),
        "stage": stage(state),
        "readiness": (presentation.verdict((state.get("last_analysis") or {}).get("decision"), contracts) or {}).get("status", ""),
        "created_at": state.get("session_created_at") or "",
    }


def save(state: Mapping[str, Any]) -> bool:
    """Persist the session and its contracts. Never raises; returns False on failure."""
    payload = session_payload(state)
    if not payload["session_id"]:
        return False
    try:
        if payload["action_contracts"]:
            state_store.save_actions(payload["action_contracts"])
        state_store.save_session(payload)
        return True
    except Exception:
        log.exception("session persist failed")
        return False


def restore(stored: Mapping[str, Any], current_actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """State updates that restore a stored session after refresh or restart."""
    step = stored.get("current_step") or "Know"
    view = stored.get("view") or "workflow"
    updates: Dict[str, Any] = {
        "ui_session_id": stored.get("session_id", ""),
        "step": step if step in STEPS else "Know",
        "view": view if view in {"workflow", "stack"} else "workflow",
        "question": stored.get("question") or workflow.DEFAULT_QUESTION,
        "persona": stored.get("persona") or scenario.PERSONA_LINE,
        "scenario_id": stored.get("scenario_id") or "go-live",
        "agent_trace": list(stored.get("agent_trace") or []),
        "agent_message": stored.get("agent_message"),
        "session_decision_id": stored.get("decision_id") or "",
        "session_action_ids": list(stored.get("selected_action_ids") or []),
        "retrieved": stored.get("retrieved"),
        "contracts_for": stored.get("contracts_for") or "",
        "session_created_at": stored.get("created_at") or "",
    }
    contracts = [a for a in (stored.get("action_contracts") or []) if isinstance(a, dict) and a.get("action_id")]
    if contracts:
        overlay = {a["action_id"]: a for a in contracts}
        merged, seen = [], set()
        for row in list(current_actions or []) + contracts:
            aid = row.get("action_id")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            merged.append({**row, **overlay.get(aid, {})})
        updates["actions"] = merged
        if not updates["session_action_ids"]:
            updates["session_action_ids"] = [a["action_id"] for a in contracts]
        try:
            for row in contracts:
                if state_store.get_action(row["action_id"]) is None:
                    state_store.save_action(row)
        except Exception:
            log.exception("session action restore failed")
    snap = stored.get("analysis")
    if snap:
        did = snap.get("decision_id") or stored.get("decision_id") or ""
        rec = state_store.get_decision(did) if did else None
        if rec is None and did:
            decision = snap.get("decision") or {}
            rec = DecisionRecord(
                decision_id=did,
                workspace_id="",
                question=stored.get("question") or workflow.DEFAULT_QUESTION,
                finding=decision.get("finding", ""),
                recommendation=decision.get("recommendation", ""),
                rationale=decision.get("rationale", ""),
                expected_outcome=decision.get("expected_outcome", ""),
            )
        updates["last_analysis"] = {**snap, "decision_record": rec, "hits": snap.get("hits") or []}
    return updates


# --------------------------------------------------------------------------
# Workflow steps (each returns state updates)
# --------------------------------------------------------------------------
def retrieval_snapshot(retriever: EnterpriseRetriever, question: str, hits) -> Dict[str, Any]:
    return {
        "question": question,
        "hits": [
            {
                "source": h.source,
                "text": h.text,
                "score": getattr(h, "score", 0.0),
                "page": getattr(h, "page", 1),
                "section": getattr(h, "section", ""),
                "snippet": getattr(h, "snippet", ""),
            }
            for h in hits
        ],
        "mode": retriever.last_mode,
        "error": getattr(retriever, "last_error", "") or "",
        "not_found": getattr(retriever, "not_found", "") or "",
    }


def retrieve(question: str, retriever: EnterpriseRetriever) -> Dict[str, Any]:
    """Know: search the workspace. Raises on retrieval failure."""
    question = question or workflow.DEFAULT_QUESTION
    hits = retriever.search(question)
    return {
        "retrieved": retrieval_snapshot(retriever, question, hits),
        "retrieve_error": "",
        "agent_trace": [f"search_knowledge · {presentation.provider_badge(retriever.last_mode)}"],
    }


def agent_trace(steps: List[Dict[str, Any]]) -> List[str]:
    """The agent's tool calls as they actually ran, e.g. "search_knowledge · 2.4 s"."""
    return [f"{s['tool']} · {s.get('ms', 0) / 1000:.1f} s" + ("" if s.get("ok", True) else " · failed") for s in steps]


def _pipeline_trace(analysis: Dict[str, Any]) -> List[str]:
    trace = [f"search_knowledge · {presentation.provider_badge(analysis.get('retrieval_mode', ''))}"]
    if analysis.get("ai_mode"):
        trace.append(str(analysis["ai_mode"]))
    return trace


def _decide(
    question: str,
    retriever: EnterpriseRetriever,
    progress: Optional[Callable[[str], None]],
    session_id: str,
    documents: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    """The agent's Decide turn, with the deterministic pipeline as fallback and second opinion."""
    from .agent import orchestrator  # the agent package imports this module's siblings

    analysis = orchestrator.run_decide(question, retriever, documents or {}, session_id, progress)
    if analysis is None:
        return workflow.run_analysis(question, retriever, progress=progress)
    if analysis["decision_record"] is None and analysis["hits"]:
        # Evidence came back but the agent found no answer in it: confirm before saying "not found".
        log.warning("Agent judged retrieved evidence does not answer %r; asking the pipeline", question[:120])
        second = workflow.run_analysis(question, retriever, progress=progress)
        if second["decision_record"] is not None:
            return second
    return analysis


def generate_brief(
    question: str,
    retriever: EnterpriseRetriever,
    progress: Optional[Callable[[str], None]] = None,
    session_id: str = "",
    documents: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Decide: the agent retrieves, reasons and records a decision. Raises on failure."""
    analysis = _decide(question, retriever, progress, session_id, documents)
    record = analysis["decision_record"]
    steps = analysis.get("agent_steps")
    return {
        "question": question,
        "last_analysis": analysis,
        "session_decision_id": record.decision_id if record else "",
        "session_action_ids": [],
        "contracts_for": "",
        "brief_error": "",
        "retrieved": retrieval_snapshot(retriever, question, analysis["hits"]),
        "agent_trace": agent_trace(steps) if steps else _pipeline_trace(analysis),
    }


def prepare_contracts(
    retriever: EnterpriseRetriever,
    record: DecisionRecord,
    decision: Dict[str, Any],
    session_id: str = "",
    documents: Optional[Dict[str, str]] = None,
    trace: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Decide → Approve: the agent proposes Action Contracts for this decision. Raises on failure."""
    from .agent import orchestrator

    result = orchestrator.run_contracts(record, decision, retriever, documents or {}, session_id or record.session_id)
    if result is not None:
        board, ai_mode = result["actions"], result["ai_mode"]
        steps = agent_trace(result["agent_steps"])
    else:
        board, ai_mode = workflow.generate_contracts(retriever, record, decision)
        steps = [f"Action Contracts · {ai_mode}"]
    scoped = [a for a in board if a.get("decision_id") == record.decision_id]
    return {
        "actions": board,
        "session_decision_id": record.decision_id,
        "session_action_ids": [a["action_id"] for a in scoped if a.get("action_id")],
        "contracts_for": record.decision_id,
        "contracts_mode": ai_mode,
        "agent_trace": list(trace or []) + steps,
    }


def suggest_outcome(
    action: Dict[str, Any],
    actual_outcome: str,
    retriever: EnterpriseRetriever,
    session_id: str = "",
    documents: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Measure: the agent proposes an outcome status and reassesses readiness; a person confirms.

    Falls back to a single Gemini assessment. Raises ai.AIServiceError when neither is available.
    """
    from . import ai
    from .agent import orchestrator

    result = orchestrator.run_reassess(action, actual_outcome, retriever, documents or {}, session_id)
    if result is not None:
        return {**result, "mode": ai.MODE_ADK, "agent_trace": agent_trace(result["agent_steps"])}
    suggestion, mode = ai.assess_outcome(action.get("expected_outcome", ""), actual_outcome)
    return {**suggestion, "summary": "", "mode": mode, "agent_trace": []}
