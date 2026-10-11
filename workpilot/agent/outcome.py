"""Outcome Tool: evaluate outcomes, reassess readiness, calculate impact.

It proposes an outcome status; a person confirms it. It never records one.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from .. import ai, presentation, state_store, workflow
from ..impact import compute_impact
from ..models import ExecutionStatus
from .run import AgentRun


def make(run: AgentRun) -> List[Callable[..., Dict[str, Any]]]:
    def evaluate_outcome(action_id: str, actual_outcome: str) -> dict:
        """Compare what actually happened with an action's expected outcome and propose a status.

        This only proposes ACHIEVED, PARTIALLY_ACHIEVED, NOT_ACHIEVED, BLOCKED or PENDING;
        a person confirms it before anything is recorded.

        Args:
            action_id: the Action Contract to evaluate.
            actual_outcome: the evidence of what happened, e.g. "p95 latency = 1.32 seconds".
        """
        action = state_store.get_action(action_id)
        if action is None:
            return {"error": f"Unknown action {action_id!r}"}
        if action.get("execution_status") != ExecutionStatus.EXECUTED:
            return {
                "eligible": False,
                "summary": f"{action.get('title')} has not been executed, so it has no outcome to evaluate yet.",
            }
        suggestion, mode = ai.assess_outcome(action.get("expected_outcome", ""), actual_outcome)
        workflow._emit(
            "outcome_evaluated",
            {"suggested": suggestion["suggested_status"], "mode": mode},
            decision_id=action.get("decision_id", ""),
            action_id=action_id,
        )
        run.proposed_outcome = {
            "suggested_status": suggestion["suggested_status"],
            "reasoning": suggestion.get("reasoning", ""),
            "mode": mode,
        }
        return {
            "eligible": True,
            "action_id": action_id,
            "expected_outcome": action.get("expected_outcome"),
            "actual_outcome": actual_outcome,
            "proposed_status": suggestion["suggested_status"],
            "reasoning": suggestion.get("reasoning", ""),
            "requires_human_confirmation": True,
            "summary": f"Proposed {suggestion['suggested_status']} for {action.get('title')}; awaiting a person's confirmation.",
        }

    def assess_readiness(decision_id: str) -> dict:
        """Reassess go-live readiness from the decision's approval conditions and recorded outcomes.

        The best possible result is READY FOR APPROVAL: final go-live approval stays with a person.

        Args:
            decision_id: the decision to reassess.
        """
        record = state_store.get_decision(decision_id)
        if record is None:
            return {"error": f"Unknown decision {decision_id!r}"}
        actions = state_store.actions_for_decision(decision_id)
        payload = {
            "readiness_status": record.readiness_status,
            "approval_conditions": record.approval_conditions,
            "blockers": [],
        }
        verdict = presentation.verdict(payload, actions) or {"status": record.readiness_status or "NOT READY", "headline": record.readiness_status or "NOT READY"}
        workflow._emit("readiness_assessed", {"status": verdict["status"]}, decision_id=decision_id)
        run.readiness = {k: verdict.get(k) for k in ("status", "headline", "open", "total")}
        return {
            "status": verdict["status"],
            "headline": verdict["headline"],
            "open": verdict.get("open"),
            "total": verdict.get("total"),
            "summary": verdict["headline"],
        }

    def calculate_impact(decision_id: str) -> dict:
        """Calculate WorkPilot's estimated productivity impact for a decision, with its assumptions.

        Args:
            decision_id: the decision to measure.
        """
        impact = compute_impact(decision_id=decision_id, documents=run.documents)
        metrics = impact["metrics"]
        minutes = float(metrics.get("estimated_total_time_saved_hours") or 0) * 60
        workflow._emit(
            "impact_calculated",
            {"hours": metrics.get("estimated_total_time_saved_hours")},
            decision_id=decision_id,
            estimated_minutes_saved=minutes,
        )
        run.impact = {"hours": metrics.get("estimated_total_time_saved_hours")}
        return {
            "metrics": metrics,
            "assumptions": impact["assumptions"],
            "summary": f"Estimated time saved: {presentation.hours_label(metrics.get('estimated_total_time_saved_hours'))} "
            "(a formula estimate, not measured time)",
        }

    return [evaluate_outcome, assess_readiness, calculate_impact]
