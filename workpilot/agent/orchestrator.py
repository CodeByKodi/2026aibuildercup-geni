"""WorkPilot Orchestrator turns: the ADK agent drives Decide and prepares Action Contracts.

Each turn is short and scoped to the tools it needs. Workflow memory lives in
Firestore, read through get_workflow_context, so a turn never depends on chat
history. A turn that fails, or ends without doing its job, returns None and the
caller runs the deterministic pipeline instead (labelled as such).

No turn can approve, reject, execute, or confirm an outcome: those tools do not exist.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .. import adk_orchestrator, ai, state_store
from ..models import DecisionRecord
from ..retrieval import EnterpriseRetriever
from . import build_tools
from .memory import GUARDRAILS
from .run import AgentRun

log = logging.getLogger("workpilot.agent")

RETRY_SECONDS = 2.0
DECIDE_TOOLS = ("get_workflow_context", "search_knowledge", "create_decision")
CONTRACT_TOOLS = ("get_workflow_context", "search_knowledge", "create_action_contracts")
REASSESS_TOOLS = ("evaluate_outcome", "assess_readiness")
RESUME_TOOLS = ("get_workflow_context", "get_action_status", "assess_readiness", "calculate_impact")

# Brief progress stages (workflow.BRIEF_STAGES): (tool, finished?) -> stage.
TOOL_STAGES = {
    ("search_knowledge", False): "retrieving",
    ("search_knowledge", True): "reasoning",
    ("create_decision", False): "structuring",
}

DECIDE_INSTRUCTION = f"""You are the WorkPilot Orchestrator, an enterprise Decision-to-Outcome agent.
This turn produces the go-live decision brief for the user's question.
1. In your first step, call get_workflow_context and search_knowledge (once, with the user's question)
   together, in parallel. If search_knowledge returns found=false, do not call create_decision;
   tell the user nothing in the workspace answers the question.
2. Reason only over the evidence returned. Never invent facts, owners, or dates; use "Unassigned" or "TBD".
3. Call create_decision exactly once. Pass every *_json argument as a JSON array string, "[]" when empty.
   List blockers in the order they should be worked, each citing source filenames exactly as returned.
   open_questions_json holds only questions the release manager must answer; never comment on the
   excerpts or on yourself. Set answer_found=false only if the evidence does not address the question.
4. Do not propose Action Contracts in this turn; the person asks for them next.
{GUARDRAILS}
"""

CONTRACT_INSTRUCTION = f"""You are the WorkPilot Orchestrator, an enterprise Decision-to-Outcome agent.
This turn turns a recorded decision into Action Contracts that a person will review.
1. In your first step, call get_workflow_context and search_knowledge (once, for the owners, deadlines and
   measurable outcomes of the decision's blockers) together, in parallel.
2. Call create_action_contracts exactly once with the decision_id you were given: one contract per blocker,
   in priority order, each with a measurable expected_outcome and one cited source filename.
   Write due dates as they appear in the evidence (e.g. "12 Oct 2026").
   Never propose go-live approval itself as an action.
{GUARDRAILS}
"""

REASSESS_INSTRUCTION = f"""You are the WorkPilot Orchestrator, an enterprise Decision-to-Outcome agent.
This turn checks one executed action's outcome and what it means for go-live.
1. Call evaluate_outcome with the action_id and the actual outcome evidence you were given, and
   assess_readiness with the decision_id, together in parallel.
2. Reply in at most two sentences: the proposed outcome status and why, then what still stands between
   the release and go-live approval. Name the action by its title, never its ID. Say that a person
   confirms the outcome; never say it is recorded.
{GUARDRAILS}
"""

RESUME_INSTRUCTION = f"""You are the WorkPilot Orchestrator, an enterprise Decision-to-Outcome agent.
You paused while people approved, executed and measured the work. This turn resumes the workflow.
1. Call get_workflow_context to read what changed in Firestore since you paused.
2. Then call get_action_status, assess_readiness and calculate_impact with the decision_id, together in parallel.
3. Reply as WorkPilot, in the first person, in at most three short sentences:
   what people have done since you paused (owners own the work; name approvers only as approved_by
   records them); the readiness status exactly as assess_readiness returned it and
   what still stands between the release and go-live approval, naming who must act; the manual baseline from
   calculate_impact, called a formula estimate. If everything is met, say all approval conditions are satisfied and the release is
   ready for approval by a person. Never say the release is approved.
{GUARDRAILS}
"""


def _steps(run: AgentRun) -> List[Dict[str, Any]]:
    return [dict(step) for step in run.trace]


def _tool_names(run: AgentRun) -> List[str]:
    return [step["tool"] for step in run.trace]


def _rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text


def _turn(run: AgentRun, instruction: str, message: str, names, final_tool: str) -> Optional[str]:
    final_tools = (final_tool,) if final_tool else ()
    try:
        try:
            return adk_orchestrator.run_turn(instruction, message, build_tools(run, names), final_tools=final_tools)
        except Exception as exc:
            # Vertex AI can briefly rate-limit. Retry once, and only if no tool ran, so nothing is recorded twice.
            if not _rate_limited(exc) or run.trace:
                raise
            log.warning("Agent turn rate-limited before any tool ran; retrying once")
            time.sleep(RETRY_SECONDS)
            return adk_orchestrator.run_turn(instruction, message, build_tools(run, names), final_tools=final_tools)
    except Exception as exc:
        adk_orchestrator.record_run(False, f"{type(exc).__name__}: {str(exc)[:160]}", _tool_names(run))
        log.warning("Agent turn failed; using the deterministic pipeline: %s", exc)
        return None


def run_decide(
    question: str,
    retriever: EnterpriseRetriever,
    documents: Dict[str, str],
    session_id: str,
    progress: Optional[Callable[[str], None]] = None,
) -> Optional[Dict[str, Any]]:
    """The agent's Decide turn. Returns an analysis shaped like workflow.run_analysis, or None to fall back."""
    if not adk_orchestrator.configured():
        return None
    started = time.perf_counter()

    def on_tool(name: str, finished: bool) -> None:
        if progress and (name, finished) in TOOL_STAGES:
            progress(TOOL_STAGES[(name, finished)])

    run = AgentRun(session_id=session_id, retriever=retriever, documents=documents, question=question, on_tool=on_tool)
    summary = _turn(run, DECIDE_INSTRUCTION, question, DECIDE_TOOLS, "create_decision")
    if summary is None:
        return None
    tools = _tool_names(run)
    searched = "search_knowledge" in tools
    base = {
        "hits": run.hits,
        "ai_mode": ai.MODE_ADK,
        "retrieval_mode": run.retrieval_mode or retriever.last_mode,
        "retrieval_error": getattr(retriever, "last_error", ""),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "agent_steps": _steps(run),
        "agent_summary": summary,
    }
    if run.decision_id:
        adk_orchestrator.record_run(True, "Decide turn recorded a decision", tools)
        record = state_store.get_decision(run.decision_id)
        return {**base, "decision_record": record, "decision": run.decision, "markdown": ai.decision_to_markdown(run.decision)}
    if searched and not run.hits:
        adk_orchestrator.record_run(True, "Nothing in the workspace answers the question", tools)
        reason = run.not_found or ai.NOT_FOUND
        return {**base, "decision_record": None, "decision": ai.not_found_decision(reason), "markdown": reason, "ai_mode": ai.MODE_NO_EVIDENCE}
    if run.answer_found is False:
        # Evidence was found but the agent judged it does not answer: the caller asks for a second opinion.
        adk_orchestrator.record_run(True, "Agent found evidence but no answer", tools)
        return {**base, "decision_record": None, "decision": ai.not_found_decision(summary.strip()), "markdown": summary or ai.NOT_FOUND}
    adk_orchestrator.record_run(False, "Decide turn ended without recording a decision", tools)
    log.warning("Agent Decide turn ended without create_decision (tools: %s)", tools)
    return None


def run_contracts(
    record: DecisionRecord,
    decision: Dict[str, Any],
    retriever: EnterpriseRetriever,
    documents: Dict[str, str],
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """The agent's contract turn. Returns {"actions", "ai_mode", "agent_steps"} or None to fall back."""
    if not adk_orchestrator.configured():
        return None
    run = AgentRun(session_id=session_id, retriever=retriever, documents=documents, question=record.question)
    blockers = [
        {k: b.get(k) for k in ("blocker", "owner", "priority", "due_date", "evidence", "recommendation")}
        for b in decision.get("blockers") or []
    ]
    message = (
        f"decision_id: {record.decision_id}\n"
        f"Question: {record.question}\n"
        f"Recommendation: {decision.get('recommendation', '')}\n"
        f"Blockers: {json.dumps(blockers, default=str)[:4000]}\n"
        "Prepare the Action Contracts for this decision."
    )
    summary = _turn(run, CONTRACT_INSTRUCTION, message, CONTRACT_TOOLS, "create_action_contracts")
    if summary is None:
        return None
    tools = _tool_names(run)
    proposed = state_store.actions_for_decision(record.decision_id)
    if not proposed:
        adk_orchestrator.record_run(False, "Contract turn ended without proposing contracts", tools)
        log.warning("Agent contract turn proposed nothing (tools: %s)", tools)
        return None
    adk_orchestrator.record_run(True, f"Contract turn proposed {len(proposed)} contracts", tools)
    return {"actions": state_store.load_actions(), "ai_mode": ai.MODE_ADK, "agent_steps": _steps(run)}


def run_reassess(
    action: Dict[str, Any],
    actual_outcome: str,
    retriever: EnterpriseRetriever,
    documents: Dict[str, str],
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """The agent's Measure turn: propose an outcome status and reassess readiness. Records nothing.

    Returns {"suggested_status", "reasoning", "summary", "agent_steps"} or None to fall back.
    """
    if not adk_orchestrator.configured():
        return None
    run = AgentRun(session_id=session_id, retriever=retriever, documents=documents)
    message = (
        f"action_id: {action.get('action_id')}\n"
        f"decision_id: {action.get('decision_id', '')}\n"
        f"Action: {action.get('title', '')}\n"
        f"Expected outcome: {action.get('expected_outcome', '')}\n"
        f"Actual outcome evidence: {actual_outcome}"
    )
    summary = _turn(run, REASSESS_INSTRUCTION, message, REASSESS_TOOLS, "")
    if summary is None:
        return None
    tools = _tool_names(run)
    if not run.proposed_outcome:
        adk_orchestrator.record_run(False, "Reassess turn ended without evaluating the outcome", tools)
        return None
    adk_orchestrator.record_run(True, "Reassess turn proposed an outcome for a person to confirm", tools)
    return {**run.proposed_outcome, "summary": summary.strip(), "agent_steps": _steps(run)}


def run_resume(
    decision_id: str,
    retriever: EnterpriseRetriever,
    documents: Dict[str, str],
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """The agent resumes after the human gate: it re-reads Firestore, reassesses readiness and impact.

    Returns {"summary", "readiness", "impact_hours", "agent_steps"} or None when the turn fails.
    The readiness shown comes from the tool's computation, never from the model's wording.
    """
    if not adk_orchestrator.configured() or not decision_id:
        return None
    run = AgentRun(session_id=session_id, retriever=retriever, documents=documents)
    summary = _turn(run, RESUME_INSTRUCTION, f"decision_id: {decision_id}\nResume the workflow.", RESUME_TOOLS, "")
    if summary is None:
        return None
    tools = _tool_names(run)
    if not run.readiness or not summary.strip():
        adk_orchestrator.record_run(False, "Resume turn ended without reassessing readiness", tools)
        return None
    adk_orchestrator.record_run(True, f"Resumed: {run.readiness.get('status')}", tools)
    return {
        "summary": summary.strip(),
        "readiness": run.readiness,
        "impact_hours": run.impact.get("hours"),
        "agent_steps": _steps(run),
    }
