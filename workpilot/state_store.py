"""Operational state store (Cloud Firestore via Firebase Admin SDK).

Firestore layout (unchanged root, extended subcollections):

    workpilot_workspaces/
        <workspace>/
            knowledge_sources/   <source_id>
            decisions/           DEC-...
            actions/             ACT-...
            executions/          EXEC-...
            audit_events/        <auto>
            impact_metrics/      <auto>
            sessions/            SES-...
            search_index/        vertex

When Firestore is unavailable (DEMO_MODE, missing credentials, outage) an
in-process memory backend with the same API keeps the application usable.
`backend_status()` reports which backend is live so the UI never claims
persistence it does not have.
"""
import logging
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import CONFIG
from .models import (
    ActionStatus,
    DecisionRecord,
    ExecutionRecord,
    ExecutionStatus,
    OutcomeStatus,
    WorkAction,
    utc_now,
)

log = logging.getLogger("workpilot.state")

ROOT = "workpilot_workspaces"

_DB = None
_INIT_ERROR: Optional[str] = None
_MEMORY = None


# --------------------------------------------------------------------------
# Minimal in-memory Firestore look-alike (fallback + tests)
# --------------------------------------------------------------------------
class _MemSnapshot:
    def __init__(self, doc_id: str, data: Optional[Dict[str, Any]]):
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> Optional[Dict[str, Any]]:
        return dict(self._data) if self._data is not None else None


class _MemQuery:
    def __init__(self, items: Dict[str, Dict[str, Any]]):
        self._items = items
        self._order: Optional[Tuple[str, bool]] = None
        self._limit: Optional[int] = None

    def order_by(self, field: str, direction: str = "ASCENDING"):
        q = _MemQuery(self._items)
        q._order = (field, str(direction).upper().endswith("DESCENDING"))
        q._limit = self._limit
        return q

    def limit(self, n: int):
        q = _MemQuery(self._items)
        q._order = self._order
        q._limit = n
        return q

    def stream(self) -> Iterable[_MemSnapshot]:
        rows = list(self._items.items())
        if self._order:
            field, desc = self._order
            rows.sort(key=lambda kv: str(kv[1].get(field, "")), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return [_MemSnapshot(k, v) for k, v in rows]


class _MemDoc:
    def __init__(self, store: Dict[str, Dict[str, Any]], doc_id: str):
        self._store = store
        self.id = doc_id

    def set(self, data: Dict[str, Any], merge: bool = False):
        if merge and self.id in self._store:
            self._store[self.id].update(data)
        else:
            self._store[self.id] = dict(data)

    def get(self) -> _MemSnapshot:
        return _MemSnapshot(self.id, self._store.get(self.id))

    def delete(self):
        self._store.pop(self.id, None)

    def collection(self, name: str):
        raise NotImplementedError("nested subcollections are not used")


class _MemCollection(_MemQuery):
    def __init__(self, items: Dict[str, Dict[str, Any]]):
        super().__init__(items)

    def document(self, doc_id: Optional[str] = None) -> _MemDoc:
        return _MemDoc(self._items, doc_id or uuid.uuid4().hex)

    def add(self, data: Dict[str, Any]):
        doc = self.document()
        doc.set(data)
        return None, doc


class MemoryDB:
    """Dictionary-backed stand-in exposing the subset of the Firestore API we use."""

    def __init__(self):
        self._data: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def workspace(self, workspace: str):
        return _MemWorkspace(self, workspace)

    def _items(self, workspace: str, name: str) -> Dict[str, Dict[str, Any]]:
        return self._data.setdefault(f"{workspace}/{name}", {})

    def clear(self):
        self._data.clear()


class _MemWorkspace:
    def __init__(self, db: MemoryDB, workspace: str):
        self._db = db
        self._ws = workspace

    def collection(self, name: str) -> _MemCollection:
        return _MemCollection(self._db._items(self._ws, name))


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------
def _firestore_enabled() -> bool:
    return CONFIG.firestore_enabled and not CONFIG.demo_mode


def get_db():
    """Return the Firestore client, or None if Firestore is not usable."""
    global _DB, _INIT_ERROR
    if _DB is not None:
        return _DB
    if not _firestore_enabled():
        return None
    try:
        import firebase_admin
        from firebase_admin import firestore

        if not firebase_admin._apps:
            project_id = CONFIG.project_id or None
            options = {"projectId": project_id} if project_id else None
            firebase_admin.initialize_app(options=options)
        _DB = firestore.client()
        return _DB
    except Exception as exc:  # keep the public demo alive if persistence is unavailable
        _INIT_ERROR = str(exc)
        log.warning("Firestore unavailable, using in-memory state: %s", exc)
        return None


def memory_db() -> MemoryDB:
    global _MEMORY
    if _MEMORY is None:
        _MEMORY = MemoryDB()
    return _MEMORY


def reset_memory():
    memory_db().clear()


def using_firestore() -> bool:
    return get_db() is not None


def backend_status() -> Tuple[str, str]:
    if CONFIG.demo_mode:
        return "Demo", "In-memory state (DEMO_MODE=true)"
    if not CONFIG.firestore_enabled:
        return "Disabled", "Firestore disabled by configuration"
    db = get_db()
    if db is not None:
        return "Connected", "Firebase Admin SDK + Cloud Firestore"
    return "Fallback", _INIT_ERROR or "Firestore unavailable; using in-memory state"


def _ws():
    db = get_db()
    if db is not None:
        return db.collection(ROOT).document(CONFIG.workspace)
    return memory_db().workspace(CONFIG.workspace)


def _col(name: str):
    return _ws().collection(name)


def _desc():
    db = get_db()
    if db is not None:
        from google.cloud.firestore_v1 import Query

        return Query.DESCENDING
    return "DESCENDING"


# --------------------------------------------------------------------------
# Knowledge sources
# --------------------------------------------------------------------------
def record_knowledge_source(source_id: str, name: str, word_count: int, parser: str, chunk_count: int = 0) -> None:
    _col("knowledge_sources").document(source_id).set(
        {
            "source_id": source_id,
            "name": name,
            "word_count": int(word_count),
            "parser": parser,
            "chunk_count": int(chunk_count),
            "workspace": CONFIG.workspace,
            "updated_at": utc_now(),
        },
        merge=True,
    )


def load_knowledge_sources() -> List[Dict[str, Any]]:
    return [d.to_dict() or {} for d in _col("knowledge_sources").stream()]


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
_STATUS_RANK = {s: i for i, s in enumerate(ActionStatus.ALL)}


def save_action(action: Dict[str, Any]) -> bool:
    """Upsert an Action Contract by action_id. Never downgrades lifecycle state.

    Returns True when written to Firestore, False when only the in-memory
    fallback received it.
    """
    model = WorkAction.from_dict(action)
    doc_ref = _col("actions").document(model.action_id)
    existing = doc_ref.get()
    doc = model.to_dict()
    if existing.exists:
        prev = WorkAction.from_dict(existing.to_dict() or {})
        # Lifecycle state (approval, execution, outcome) belongs to one decision.
        # A contract for a different decision never inherits it.
        same_decision = not model.decision_id or not prev.decision_id or prev.decision_id == model.decision_id
        if same_decision and _STATUS_RANK.get(prev.status, 0) > _STATUS_RANK.get(model.status, 0):
            for key in (
                "status",
                "approval_status",
                "approved_by",
                "approved_at",
                "execution_status",
                "actual_outcome",
                "outcome_status",
            ):
                doc[key] = getattr(prev, key)
        doc["created_at"] = prev.created_at or doc["created_at"]
        if prev.decision_id and not doc.get("decision_id"):
            doc["decision_id"] = prev.decision_id
    doc["workspace"] = CONFIG.workspace
    doc["updated_at"] = utc_now()
    doc_ref.set(doc, merge=True)
    action.update({k: doc[k] for k in ("action_id", "status", "approval_status") if k in doc})
    return using_firestore()


def save_actions(actions: List[Dict[str, Any]]) -> int:
    return sum(1 for action in actions if save_action(action))


def load_actions() -> List[Dict[str, Any]]:
    """Load actions, collapsing legacy duplicates onto their canonical action_id."""
    merged: Dict[str, WorkAction] = {}
    for snap in _col("actions").stream():
        data = snap.to_dict() or {}
        data.pop("workspace", None)
        model = WorkAction.from_dict(data)
        current = merged.get(model.action_id)
        if current is None or _STATUS_RANK.get(model.status, 0) > _STATUS_RANK.get(current.status, 0):
            merged[model.action_id] = model
    items = [m.to_dict() for m in merged.values()]
    priority_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    items.sort(key=lambda a: (priority_order.get(a.get("priority"), 99), a.get("due_date", "")))
    return items


def get_action(action_id: str) -> Optional[Dict[str, Any]]:
    snap = _col("actions").document(action_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data.pop("workspace", None)
    return WorkAction.from_dict(data).to_dict()


def transition_action(action_id: str, new_status: str, **fields: Any) -> Tuple[bool, str]:
    current = get_action(action_id)
    if current is None:
        return False, f"Action {action_id} not found"
    if not ActionStatus.can_transition(current["status"], new_status):
        return False, f"Cannot move {current['status']} → {new_status}"
    update = {"status": new_status, "updated_at": utc_now(), **fields}
    _col("actions").document(action_id).set(update, merge=True)
    return True, new_status


def approve_action(action_id: str, approved_by: str = "Human reviewer") -> Tuple[bool, str]:
    return transition_action(
        action_id,
        ActionStatus.APPROVED,
        approval_status=f"Approved by {approved_by}",
        approved_by=approved_by,
        approved_at=utc_now(),
    )


def reject_action(action_id: str, rejected_by: str = "Human reviewer") -> Tuple[bool, str]:
    return transition_action(
        action_id,
        ActionStatus.REJECTED,
        approval_status=f"Rejected by {rejected_by}",
    )


def set_execution_status(action_id: str, execution_status: str) -> None:
    _col("actions").document(action_id).set({"execution_status": execution_status, "updated_at": utc_now()}, merge=True)


_OUTCOME_ELIGIBLE = {
    ActionStatus.APPROVED,
    ActionStatus.IN_PROGRESS,
    ActionStatus.BLOCKED,
    ActionStatus.COMPLETED,
    ActionStatus.FAILED,
    ActionStatus.VERIFIED,
}


def record_outcome(action_id: str, actual_outcome: str, outcome_status: str) -> Tuple[bool, str]:
    """Human outcome verification. Sets lifecycle status from the outcome.

    Only an approved action whose follow-up actually executed can carry an
    outcome, so Measure never reports ACHIEVED next to "Not started".
    """
    current = get_action(action_id)
    if current is None:
        return False, f"Action {action_id} not found"
    if outcome_status not in OutcomeStatus.ALL:
        return False, f"Unknown outcome status {outcome_status}"
    if current["status"] not in _OUTCOME_ELIGIBLE:
        return False, "Approve this action before recording an outcome"
    if current.get("execution_status") != ExecutionStatus.EXECUTED:
        return False, "Execute this action before recording an outcome"
    status_map = {
        OutcomeStatus.ACHIEVED: ActionStatus.VERIFIED,
        OutcomeStatus.PARTIALLY_ACHIEVED: ActionStatus.VERIFIED,
        OutcomeStatus.NOT_ACHIEVED: ActionStatus.FAILED,
        OutcomeStatus.BLOCKED: ActionStatus.BLOCKED,
    }
    update: Dict[str, Any] = {
        "actual_outcome": actual_outcome,
        "outcome_status": outcome_status,
        "updated_at": utc_now(),
    }
    if outcome_status in status_map:
        update["status"] = status_map[outcome_status]
    _col("actions").document(action_id).set(update, merge=True)
    return True, update.get("status", current["status"])


# --------------------------------------------------------------------------
# Decisions (Decision Memory)
# --------------------------------------------------------------------------
def save_decision(decision: DecisionRecord) -> bool:
    doc = decision.to_dict()
    doc["updated_at"] = utc_now()
    _col("decisions").document(decision.decision_id).set(doc, merge=True)
    return using_firestore()


def update_decision(decision_id: str, **fields: Any) -> None:
    fields["updated_at"] = utc_now()
    _col("decisions").document(decision_id).set(fields, merge=True)


def get_decision(decision_id: str) -> Optional[DecisionRecord]:
    snap = _col("decisions").document(decision_id).get()
    if not snap.exists:
        return None
    return DecisionRecord.from_dict(snap.to_dict() or {})


def load_decisions(limit: int = 20) -> List[DecisionRecord]:
    query = _col("decisions").order_by("created_at", direction=_desc()).limit(limit)
    return [DecisionRecord.from_dict(d.to_dict() or {}) for d in query.stream()]


def link_actions_to_decision(decision_id: str, action_ids: List[str]) -> None:
    decision = get_decision(decision_id)
    if decision is None:
        return
    merged = list(dict.fromkeys(list(decision.actions) + list(action_ids)))
    update_decision(decision_id, actions=merged, status="ACTIONED" if merged else decision.status)


def actions_for_decision(decision_id: str) -> List[Dict[str, Any]]:
    return [a for a in load_actions() if a.get("decision_id") == decision_id]


# --------------------------------------------------------------------------
# Executions
# --------------------------------------------------------------------------
def save_execution(record: ExecutionRecord) -> bool:
    doc = record.to_dict()
    doc["workspace"] = CONFIG.workspace
    _col("executions").document(record.execution_id).set(doc, merge=True)
    return using_firestore()


def load_executions(limit: int = 50) -> List[ExecutionRecord]:
    query = _col("executions").order_by("created_at", direction=_desc()).limit(limit)
    return [ExecutionRecord.from_dict(d.to_dict() or {}) for d in query.stream()]


# --------------------------------------------------------------------------
# Audit events
# --------------------------------------------------------------------------
def log_event(
    event_type: str,
    payload: Dict[str, Any],
    decision_id: str = "",
    action_id: str = "",
    estimated_minutes_saved: float = 0.0,
) -> bool:
    event = {
        "event_id": uuid.uuid4().hex,
        "event_type": event_type,
        "payload": payload,
        "decision_id": decision_id,
        "action_id": action_id,
        "estimated_minutes_saved": float(estimated_minutes_saved),
        "workspace": CONFIG.workspace,
        "created_at": utc_now(),
    }
    _col("audit_events").add(event)
    return using_firestore()


def load_events(limit: int = 20) -> List[Dict[str, Any]]:
    query = _col("audit_events").order_by("created_at", direction=_desc()).limit(limit)
    return [doc.to_dict() or {} for doc in query.stream()]


# --------------------------------------------------------------------------
# Impact snapshots
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# UI sessions (refresh / reopen restore)
# --------------------------------------------------------------------------
def save_session(session: Dict[str, Any]) -> bool:
    """Upsert one browser workflow session. Historical sessions are kept."""
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        return False
    doc = dict(session)
    doc["session_id"] = session_id
    doc["workspace"] = CONFIG.workspace
    doc["updated_at"] = utc_now()
    doc.setdefault("created_at", utc_now())
    _col("sessions").document(session_id).set(doc, merge=True)
    return using_firestore()


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    snap = _col("sessions").document(session_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data.pop("workspace", None)
    return data


# Documents a person uploaded into one session. The text lives in its own document
# (Firestore caps a document at 1 MiB); the session lists what it holds.
SESSION_DOC_CHARS = 700_000


def _session_doc_id(session_id: str, source_id: str) -> str:
    return f"{session_id}__{source_id}"


def save_session_document(session_id: str, source_id: str, name: str, text: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    truncated = len(text) > SESSION_DOC_CHARS
    entry = {
        "source_id": source_id,
        "name": name,
        "parser": str(meta.get("parser") or ""),
        "words": int(meta.get("word_count") or 0),
        "chunks": int(meta.get("chunks") or 0),
        "bytes": int(meta.get("bytes") or 0),
        "duration_ms": int(meta.get("duration_ms") or 0),
        "truncated": truncated,
        "uploaded_at": utc_now(),
    }
    _col("session_documents").document(_session_doc_id(session_id, source_id)).set(
        {**entry, "session_id": session_id, "text": text[:SESSION_DOC_CHARS], "workspace": CONFIG.workspace}
    )
    current = [u for u in (get_session(session_id) or {}).get("uploads") or [] if u.get("source_id") != source_id]
    _col("sessions").document(session_id).set({"uploads": current + [entry], "updated_at": utc_now()}, merge=True)
    return entry


def delete_session_document(session_id: str, source_id: str) -> bool:
    stored = get_session(session_id) or {}
    uploads = stored.get("uploads") or []
    kept = [u for u in uploads if u.get("source_id") != source_id]
    if len(kept) == len(uploads):
        return False
    _col("session_documents").document(_session_doc_id(session_id, source_id)).delete()
    _col("sessions").document(session_id).set({"uploads": kept, "updated_at": utc_now()}, merge=True)
    return True


def load_session_documents(session_id: str, uploads: List[Dict[str, Any]]) -> Dict[str, str]:
    """name -> text for the uploads a session lists."""
    out: Dict[str, str] = {}
    for entry in uploads or []:
        snap = _col("session_documents").document(_session_doc_id(session_id, str(entry.get("source_id") or ""))).get()
        if snap.exists:
            data = snap.to_dict() or {}
            out[str(data.get("name") or entry.get("name"))] = str(data.get("text") or "")
    return out


def save_search_index_state(fingerprint: str, document_names: List[str]) -> None:
    _col("search_index").document("vertex").set(
        {
            "fingerprint": fingerprint,
            "document_names": list(document_names),
            "workspace": CONFIG.workspace,
            "updated_at": utc_now(),
        },
        merge=True,
    )


def get_search_index_state() -> Optional[Dict[str, Any]]:
    snap = _col("search_index").document("vertex").get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data.pop("workspace", None)
    return data


def list_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    query = _col("sessions").order_by("updated_at", direction=_desc()).limit(limit)
    rows = []
    for snap in query.stream():
        data = snap.to_dict() or {}
        data.pop("workspace", None)
        rows.append(data)
    return rows


def executions_for_action(action_id: str) -> List[ExecutionRecord]:
    return [e for e in load_executions(limit=200) if e.action_id == action_id]
