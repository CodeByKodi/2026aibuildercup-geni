"""Action Tool: propose Action Contracts and read their status. It can never approve or execute."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from .. import ai, presentation, state_store, workflow
from .run import AgentRun


def _contract_view(action: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action_id": action.get("action_id"),
        "title": action.get("title"),
        "owner": action.get("owner"),
        "due_date": action.get("due_date"),
        "priority": action.get("priority"),
        "approval": presentation.approval_label(action.get("status", "")),
        "approved_by": action.get("approved_by") or "",
        "execution": presentation.execution_label(action),
        "outcome": action.get("outcome_status") or "PENDING",
    }


def make(run: AgentRun) -> List[Callable[..., Dict[str, Any]]]:
    def create_action_contracts(decision_id: str, actions_json: str) -> dict:
        """Propose Action Contracts for a recorded decision. They wait for a person to approve them.

        One contract per blocker. Never propose go-live approval itself as an action: that
        decision stays with a person after outcomes are verified.

        Args:
            decision_id: the decision_id returned by create_decision.
            actions_json: JSON array of actions, each with title, owner, due_date, priority (Critical|High|Medium|Low), rationale, source (one source filename), expected_outcome (measurable). Use "Unassigned" or "TBD" when the evidence does not say.
        """
        record = state_store.get_decision(decision_id)
        if record is None:
            return {"error": f"Unknown decision {decision_id!r}. Call create_decision first."}
        if record.session_id and record.session_id != run.session_id:
            return {"error": "That decision belongs to a different session."}
        try:
            data = json.loads(actions_json) if actions_json else []
        except json.JSONDecodeError as exc:
            return {"error": f"actions_json is not valid JSON: {exc}"}
        actions = [a for a in ai.parse_actions(data, decision_id) if a.title and a.title != "Untitled action"]
        if not actions:
            return {"error": "No valid actions: each needs at least a title."}
        known = run.evidence_sources() | {e.get("source") for e in record.evidence}
        for action in actions:  # cite only evidence that was actually retrieved
            action.evidence = [e for e in action.evidence if e in known]
            if action.source not in known:
                action.source = action.evidence[0] if action.evidence else "TBD"
        workflow.propose_contracts(actions, decision_id, ai.MODE_ADK, run.retrieval_mode, run.started)
        contracts = [_contract_view(a) for a in state_store.actions_for_decision(decision_id)]
        return {
            "proposed": contracts,
            "awaiting_human_approval": True,
            "summary": f"{len(contracts)} Action Contract(s) proposed for {decision_id}; a person must approve each one.",
        }

    def get_action_status(decision_id: str) -> dict:
        """Read the approval, execution and outcome status of a decision's Action Contracts.

        Args:
            decision_id: the decision whose contracts to read.
        """
        actions = state_store.actions_for_decision(decision_id)
        progress = presentation.board_progress(actions)
        pending = [a["action_id"] for a in actions if a.get("status") == "PROPOSED"]
        return {
            "contracts": [_contract_view(a) for a in actions],
            "progress": progress,
            "awaiting_human_approval": pending,
            "summary": f"{progress['approved']} approved, {progress['executed']} executed, "
            f"{progress['measured']} measured, {progress['pending']} awaiting approval",
        }

    return [create_action_contracts, get_action_status]
