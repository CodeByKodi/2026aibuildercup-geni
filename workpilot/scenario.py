"""Enterprise Release Readiness demo scenario.

Sample documents are the source of truth. This module only names the
scenario, matches recorded outcomes, and derives go-live readiness from
Action Contract state. It does not invent blockers or owners.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from .models import ActionStatus, OutcomeStatus

_HUMAN_GATE = re.compile(
    r"go[\s/_-]*no[\s/_-]*go|production go-?live|approve (the )?(production|release|go-live)|final (go-live )?approval",
    re.I,
)
_REMEDIATION = re.compile(
    r"\b(sec|pay|perf|uat)-\d+\b|\b(validat|retest|remediat|defect|sign-?off|p95|cache)\b",
    re.I,
)

SCENARIO_TITLE = "Enterprise Release Readiness"
RELEASE_NAME = "Customer Portal v12.4"
TARGET_GO_LIVE = "18 Oct 2026"

QUESTION = (
    "Are we ready to go live? What is blocking the release, what should we "
    "prioritize this week, who owns each action, and what must be completed before approval?"
)

PERSONA_NAME = "Maya Chen"
PERSONA_ROLE = "Release Manager"
PERSONA_LINE = (
    f"You are {PERSONA_NAME}, {PERSONA_ROLE} for {RELEASE_NAME} — "
    "deciding whether this release can go live."
)

SCENARIOS = (
    {
        "id": "go-live",
        "label": "Go-live readiness",
        "persona": f"{PERSONA_NAME}, {PERSONA_ROLE}",
        "question": QUESTION,
        "hint": "Amber release. What blocks 18 Oct?",
    },
    {
        "id": "sec-217",
        "label": "Security sign-off",
        "persona": "Mei Tanaka, Security Lead",
        "question": (
            "What is blocking security sign-off for Customer Portal v12.4? "
            "Who owns SEC-217 and what must be verified before go-live?"
        ),
        "hint": "SEC-217 session-token risk",
    },
    {
        "id": "uat-perf",
        "label": "UAT and p95",
        "persona": "Arjun Mehta, Performance Engineer",
        "question": (
            "Is Customer Profile API p95 acceptable for go-live, and what must "
            "close in UAT (PAY-482) before 18 Oct?"
        ),
        "hint": "PAY-482 and 1.5s p95",
    },
)


# The six synthetic documents of the Release Readiness workspace (sample_data/).
SOURCE_FILES = (
    "release_status_report.md",
    "security_assessment.md",
    "performance_test_report.md",
    "uat_meeting_notes.md",
    "issue_tracker.md",
    "architecture_note.md",
)

# Only a fully achieved outcome closes a blocker; "partially achieved" leaves it open.
_DONE = {OutcomeStatus.ACHIEVED}
_CLOSED_STATUS = {ActionStatus.VERIFIED, ActionStatus.COMPLETED}


def is_human_gate_action(action: Dict[str, Any]) -> bool:
    """True only for go-live authorization ceremonies, never remediations.

    Expected outcomes often mention production go-live as the goal. Those
    remain Action Contracts. Only the title is inspected.
    """
    title = str(action.get("title") or action.get("action") or "").strip()
    if not title or _REMEDIATION.search(title):
        return False
    return bool(_HUMAN_GATE.search(title))


def _closed(action: Dict[str, Any]) -> bool:
    """Closed by an ACHIEVED outcome, or by a lifecycle close when no outcome is recorded yet.

    A recorded outcome decides: PARTIALLY_ACHIEVED is stored with status VERIFIED but stays open.
    """
    outcome = str(action.get("outcome_status") or OutcomeStatus.PENDING)
    if outcome != OutcomeStatus.PENDING:
        return outcome in _DONE
    return action.get("status") in _CLOSED_STATUS


def readiness_from_actions(
    actions: Sequence[Dict[str, Any]],
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Human approval of production is never implied. This is a readiness label only."""
    board = [a for a in list(actions or []) if not is_human_gate_action(a)]
    blockers = []
    if analysis:
        blockers = [b for b in (analysis.get("blockers") or []) if isinstance(b, dict)]
    if not board and not blockers:
        return {
            "status": "NOT STARTED",
            "remaining": 0,
            "summary": "No go-live decision has been recorded in this session.",
            "approval_conditions": [],
        }

    open_actions = [
        a
        for a in board
        if a.get("status") != ActionStatus.REJECTED
        and not _closed(a)
    ]
    conditions = []
    for a in board:
        title = str(a.get("title") or "Action")
        owner = str(a.get("owner") or "Unassigned")
        outcome = str(a.get("actual_outcome") or a.get("outcome_status") or "Pending")
        conditions.append(f"{title} · {owner} · {outcome}")

    if board and not open_actions:
        return {
            "status": "READY FOR APPROVAL",
            "remaining": 0,
            "summary": "Recorded outcomes close the release blockers. Production release still needs a human go-live approval.",
            "approval_conditions": conditions,
        }
    remaining = len(open_actions) or len(blockers)
    return {
        "status": "NOT READY",
        "remaining": remaining,
        "summary": f"{remaining} approval condition(s) remain incomplete. Do not approve production release yet.",
        "approval_conditions": conditions or [str(b.get("blocker") or "") for b in blockers if b.get("blocker")],
    }


def serialize_hits(hits: Sequence[Any]) -> List[Dict[str, Any]]:
    rows = []
    for hit in hits or []:
        get = (lambda k: hit.get(k)) if isinstance(hit, dict) else (lambda k: getattr(hit, k, None))
        rows.append(
            {
                "source": str(get("source") or ""),
                "text": str(get("text") or get("excerpt") or ""),
                "score": float(get("score") or 0),
                "page": int(get("page") or 1),
                "section": str(get("section") or ""),
                "snippet": str(get("snippet") or ""),
            }
        )
    return rows


def snapshot_analysis(analysis: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not analysis:
        return None
    record = analysis.get("decision_record")
    decision_id = ""
    if record is not None:
        decision_id = getattr(record, "decision_id", "") or ""
    decision_id = decision_id or str(analysis.get("decision_id") or "")
    return {
        "decision": analysis.get("decision") or {},
        "hits": serialize_hits(analysis.get("hits") or []),
        "ai_mode": analysis.get("ai_mode") or "",
        "retrieval_mode": analysis.get("retrieval_mode") or "",
        "duration_ms": int(analysis.get("duration_ms") or 0),
        "decision_id": decision_id,
    }
