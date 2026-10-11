"""Workflow memory: where this session stands, read from Firestore, so an agent turn can resume."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from .. import presentation, state_store, workflow_session
from .run import AgentRun

GUARDRAILS = (
    "You cannot approve, reject or execute actions, or confirm outcomes. "
    "People do that in the WorkPilot UI. When the stage is APPROVAL or OUTCOME, stop and say what the person must do."
)


def make(run: AgentRun) -> List[Callable[..., Dict[str, Any]]]:
    def get_workflow_context() -> dict:
        """Read this session's saved workflow: current stage, question, decision, and contract status.

        Call this at the start of a turn to continue where the workflow stopped.
        """
        stored = state_store.get_session(run.session_id)
        if not stored:
            return {
                "stage": "KNOWLEDGE",
                "next_step": workflow_session.next_step("KNOWLEDGE"),
                "guardrails": GUARDRAILS,
                "summary": "New session: nothing recorded yet.",
            }
        state = workflow_session.restore(stored, state_store.load_actions())
        current = workflow_session.stage(state)
        board = workflow_session.session_board(state)
        progress = presentation.board_progress(board)
        return {
            "stage": current,
            "next_step": workflow_session.next_step(current),
            "question": state.get("question"),
            "decision_id": state.get("session_decision_id") or "",
            "progress": progress,
            "awaiting_human_approval": [a["action_id"] for a in board if a.get("status") == "PROPOSED"],
            "guardrails": GUARDRAILS,
            "summary": f"Stage {current}: {workflow_session.next_step(current)}",
        }

    return [get_workflow_context]
