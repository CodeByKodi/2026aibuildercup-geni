"""Decision Tool: persist an evidence-backed DecisionRecord from Gemini's reasoning."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from .. import ai, presentation, workflow
from .run import AgentRun, elapsed_ms


def _json_list(raw: str, field: str) -> List[Any]:
    try:
        value = json.loads(raw) if raw else []
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} is not valid JSON: {exc}") from exc
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return value


def make(run: AgentRun) -> List[Callable[..., Dict[str, Any]]]:
    def create_decision(
        finding: str,
        recommendation: str,
        rationale: str,
        expected_outcome: str,
        readiness_status: str,
        blockers_json: str,
        approval_conditions_json: str,
        open_questions_json: str,
        answer_found: bool,
    ) -> dict:
        """Record the go-live decision as a DecisionRecord grounded in the retrieved evidence.

        Call exactly once, after search_knowledge. Use only facts from the evidence and cite
        source filenames exactly as search_knowledge returned them. Never mark a release approved.

        Args:
            finding: one-paragraph grounded answer to the user's question.
            recommendation: what to prioritise, in the order work should happen.
            rationale: why the recommendation follows from the evidence.
            expected_outcome: the measurable result if the recommendation is followed.
            readiness_status: NOT READY, AT RISK, or READY FOR APPROVAL.
            blockers_json: JSON array of blockers, each with blocker, evidence (array of source filenames), business_impact, recommendation, owner, priority (Critical|High|Medium|Low), due_date. Use "[]" if none.
            approval_conditions_json: JSON array of strings, one sentence per condition that must hold before a person approves go-live. Use "[]" if none.
            open_questions_json: JSON array of strings, each a question only a person can answer. Use "[]" if none.
            answer_found: false when the evidence does not answer the question; nothing is recorded then.
        """
        if not run.hits:
            return {"error": "No evidence yet. Call search_knowledge first; a decision must be grounded in evidence."}
        run.answer_found = bool(answer_found)
        if not answer_found:
            return {"recorded": False, "summary": "Not found in workspace sources: no decision recorded, no blockers created."}
        raw = {
            "answer_found": True,
            "finding": finding,
            "recommendation": recommendation,
            "rationale": rationale,
            "expected_outcome": expected_outcome,
            "readiness_status": readiness_status,
            "blockers": _json_list(blockers_json, "blockers_json"),
            "approval_conditions": _json_list(approval_conditions_json, "approval_conditions_json"),
            "open_questions": _json_list(open_questions_json, "open_questions_json"),
        }
        decision = ai.normalize_decision(raw)
        # Citations must point at evidence this run actually retrieved.
        known = run.evidence_sources()
        dropped = 0
        for blocker in decision["blockers"]:
            cited = [s for s in blocker.get("evidence") or [] if s in known]
            dropped += len(blocker.get("evidence") or []) - len(cited)
            blocker["evidence"] = cited
        record = workflow.record_decision(
            run.question,
            decision,
            run.hits,
            ai.MODE_ADK,
            run.retrieval_mode,
            duration_ms=elapsed_ms(run),
            session_id=run.session_id,
        )
        run.decision_id = record.decision_id
        run.decision = decision
        verdict = presentation.verdict(decision)
        result = {
            "recorded": True,
            "decision_id": record.decision_id,
            "verdict": verdict["headline"] if verdict else readiness_status,
            "blocker_count": len(decision["blockers"]),
            "approval_conditions": decision["approval_conditions"],
            "summary": f"Decision {record.decision_id}: {verdict['headline'] if verdict else readiness_status}",
        }
        if dropped:
            result["note"] = f"{dropped} citation(s) were not in the retrieved evidence and were removed."
        return result

    return [create_decision]
