"""Core WorkPilot data model.

Know → Decide → Approve → Act → Measure

DecisionRecord  : why a decision was made, on what evidence, by whom
WorkAction      : an Action Contract derived from a decision
ExecutionRecord : receipt for a follow-up executed after human approval
"""
import hashlib
import re
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_hash(*parts: str, length: int = 8) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


# --------------------------------------------------------------------------
# Status vocabularies
# --------------------------------------------------------------------------
class ActionStatus:
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"

    ALL = [PROPOSED, APPROVED, REJECTED, IN_PROGRESS, BLOCKED, COMPLETED, FAILED, VERIFIED]

    # Deliberately simple state machine.
    TRANSITIONS = {
        PROPOSED: {APPROVED, REJECTED},
        APPROVED: {IN_PROGRESS, BLOCKED, COMPLETED, FAILED},
        REJECTED: set(),
        IN_PROGRESS: {BLOCKED, COMPLETED, FAILED},
        BLOCKED: {IN_PROGRESS, FAILED},
        COMPLETED: {VERIFIED},
        FAILED: {IN_PROGRESS},
        VERIFIED: set(),
    }

    # Values written by the previous version of the app.
    LEGACY = {
        "Proposed": PROPOSED,
        "In Progress": IN_PROGRESS,
        "Approved": APPROVED,
        "Rejected": REJECTED,
        "Completed": COMPLETED,
    }

    @classmethod
    def normalize(cls, value: Optional[str]) -> str:
        if not value:
            return cls.PROPOSED
        if value in cls.ALL:
            return value
        if value in cls.LEGACY:
            return cls.LEGACY[value]
        candidate = value.upper().replace(" ", "_")
        return candidate if candidate in cls.ALL else cls.PROPOSED

    @classmethod
    def can_transition(cls, current: str, new: str) -> bool:
        return new in cls.TRANSITIONS.get(cls.normalize(current), set())


class OutcomeStatus:
    PENDING = "PENDING"
    ACHIEVED = "ACHIEVED"
    PARTIALLY_ACHIEVED = "PARTIALLY_ACHIEVED"
    NOT_ACHIEVED = "NOT_ACHIEVED"
    BLOCKED = "BLOCKED"
    ALL = [PENDING, ACHIEVED, PARTIALLY_ACHIEVED, NOT_ACHIEVED, BLOCKED]


class ExecutionStatus:
    NOT_STARTED = "NOT_STARTED"
    EXECUTED = "EXECUTED"  # a real Gmail/Calendar API call returned
    SUCCESS = "SUCCESS"  # receipt status for a successful adapter call
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ExecutionMode:
    REAL = "REAL"
    DEMO = "DEMO"


PRIORITIES = ["Critical", "High", "Medium", "Low"]


# --------------------------------------------------------------------------
# Knowledge
# --------------------------------------------------------------------------
@dataclass
class SourceChunk:
    source: str
    text: str
    score: float = 0.0
    page: int = 1
    section: str = ""
    snippet: str = ""  # the passage to display; `text` is the fuller context for Gemini


@dataclass
class DocumentChunk:
    """Normalized output of any DocumentProcessor implementation."""

    source_id: str
    source_name: str
    text: str
    page: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_text(value: Any, joiner: str = ", ") -> str:
    """Gemini occasionally returns lists where a string was requested."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return joiner.join(_as_text(v, joiner) for v in value if v is not None)
    if isinstance(value, dict):
        return joiner.join(f"{k}: {_as_text(v, joiner)}" for k, v in value.items())
    return str(value).strip()


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_as_text(v) for v in value if _as_text(v)]
    text = _as_text(value)
    return [text] if text else []


def _norm_priority(value: Any) -> str:
    text = _as_text(value).capitalize()
    return text if text in PRIORITIES else "Medium"


# --------------------------------------------------------------------------
# Action Contract
# --------------------------------------------------------------------------
@dataclass
class WorkAction:
    title: str
    owner: str
    due_date: str
    priority: str
    rationale: str
    source: str
    expected_outcome: str = "TBD"
    approval_status: str = "Pending human approval"
    status: str = ActionStatus.PROPOSED
    action_id: str = ""
    decision_id: str = ""
    evidence: List[str] = field(default_factory=list)
    execution_status: str = ExecutionStatus.NOT_STARTED
    actual_outcome: str = ""
    outcome_status: str = OutcomeStatus.PENDING
    approved_by: str = ""
    approved_at: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self):
        self.title = _as_text(self.title) or "Untitled action"
        self.owner = _as_text(self.owner) or "Unassigned"
        self.due_date = _as_text(self.due_date) or "TBD"
        self.priority = _norm_priority(self.priority)
        self.rationale = _as_text(self.rationale)
        self.source = _as_text(self.source) or "TBD"
        self.expected_outcome = _as_text(self.expected_outcome) or "TBD"
        self.status = ActionStatus.normalize(self.status)
        self.evidence = _as_list(self.evidence)
        if not self.evidence and self.source and self.source != "TBD":
            self.evidence = [s.strip() for s in self.source.split(",") if s.strip()]
        if not self.action_id:
            self.action_id = make_action_id(self.title, self.owner, self.decision_id)

    @classmethod
    def from_llm(cls, item: Dict[str, Any], decision_id: str = "") -> "WorkAction":
        """Build a contract from loosely-structured model output.

        Unknown keys are ignored; list-valued fields are flattened; priority
        is clamped to the allowed vocabulary.
        """
        item = item or {}
        return cls(
            title=item.get("title") or item.get("action") or "",
            owner=item.get("owner", ""),
            due_date=item.get("due_date") or item.get("due", ""),
            priority=item.get("priority", "Medium"),
            rationale=item.get("rationale", ""),
            source=item.get("source") or item.get("evidence", ""),
            expected_outcome=item.get("expected_outcome", "TBD"),
            evidence=item.get("evidence", []),
            decision_id=decision_id,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkAction":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in allowed}
        clean.setdefault("title", "")
        clean.setdefault("owner", "")
        clean.setdefault("due_date", "")
        clean.setdefault("priority", "Medium")
        clean.setdefault("rationale", "")
        clean.setdefault("source", "")
        return cls(**clean)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_action_id(title: str, owner: str, decision_id: str = "") -> str:
    """Stable within one decision, distinct across decisions.

    Scoping by decision keeps a new session's contract from colliding with an
    identical contract approved (or verified) in an earlier session.
    """
    parts = [_norm(title), _norm(owner)]
    if decision_id:
        parts.append(decision_id)
    return "ACT-" + _short_hash(*parts)


# --------------------------------------------------------------------------
# Decision Memory
# --------------------------------------------------------------------------
@dataclass
class DecisionRecord:
    decision_id: str
    workspace_id: str
    question: str
    finding: str
    recommendation: str
    rationale: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    approved_by: str = ""
    approved_at: str = ""
    actions: List[str] = field(default_factory=list)
    expected_outcome: str = ""
    actual_outcome: str = ""
    status: str = "OPEN"  # OPEN -> ACTIONED -> CLOSED
    ai_mode: str = ""
    retrieval_mode: str = ""
    session_id: str = ""
    readiness_status: str = ""
    approval_conditions: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self):
        self.finding = _as_text(self.finding)
        self.recommendation = _as_text(self.recommendation)
        self.rationale = _as_text(self.rationale)
        self.expected_outcome = _as_text(self.expected_outcome)
        self.actual_outcome = _as_text(self.actual_outcome)
        self.approval_conditions = _as_list(self.approval_conditions)
        cleaned = []
        for ev in self.evidence or []:
            if isinstance(ev, dict):
                cleaned.append({"source": _as_text(ev.get("source")), "excerpt": _as_text(ev.get("excerpt") or ev.get("text"))[:600]})
            else:
                cleaned.append({"source": _as_text(ev), "excerpt": ""})
        self.evidence = cleaned

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionRecord":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in allowed}
        for key in ("decision_id", "workspace_id", "question", "finding", "recommendation", "rationale"):
            clean.setdefault(key, "")
        return cls(**clean)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_decision_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"DEC-{stamp}-{uuid.uuid4().hex[:4]}"


# --------------------------------------------------------------------------
# Execution receipt
# --------------------------------------------------------------------------
@dataclass
class ExecutionRecord:
    execution_id: str
    action_id: str
    decision_id: str
    execution_type: str  # gmail_draft | calendar_followup
    target: str
    status: str  # SUCCESS | FAILED | UNAVAILABLE
    mode: str = ExecutionMode.DEMO  # REAL | DEMO
    created_at: str = field(default_factory=utc_now)
    external_reference: str = ""
    error: str = ""
    summary: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionRecord":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in allowed}
        for key in ("execution_id", "action_id", "decision_id", "execution_type", "target", "status"):
            clean.setdefault(key, "")
        return cls(**clean)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_execution_id() -> str:
    return "EXEC-" + uuid.uuid4().hex[:10]
