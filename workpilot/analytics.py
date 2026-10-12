"""BigQuery event analytics (impact measurement).

Firestore holds operational state; BigQuery holds the append-only event
stream. Writes are best-effort: a BigQuery failure is logged and the
workflow continues.

Event schema:
    event_id, event_type, workspace_id, decision_id, action_id,
    source_count, duration_ms, estimated_minutes_saved, timestamp, metadata
"""
import contextvars
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .config import CONFIG

log = logging.getLogger("workpilot.analytics")

EVENT_TYPES = {
    "knowledge_analyzed",
    "document_processed",
    "search_performed",
    "decision_supported",
    "decision_created",
    "blocker_identified",
    "action_identified",
    "action_contract_created",
    "action_approved",
    "gmail_draft_created",
    "calendar_followup_created",
    "action_completed",
    "outcome_verified",
    "outcome_recorded",
    "release_readiness_updated",
    "execution_failed",
    "execution_unavailable",
    "action_rejected",
    "brief_emailed",
    "outcome_evaluated",
    "readiness_assessed",
    "impact_calculated",
}

_TABLE_READY = False
_TABLE = None
_CLIENT = None
_LAST_ERROR = ""
_VERIFIED: Optional[Tuple[bool, str]] = None

# The UI session that emitted an event, so Measure can say how many of *its*
# events reached BigQuery. Set once per API request; copied into worker threads.
_SESSION: contextvars.ContextVar[str] = contextvars.ContextVar("workpilot_session", default="")
_WRITTEN: Dict[str, int] = {}
_COUNT_LOCK = threading.Lock()
# One writer thread keeps inserts off the request path and in order.
_WRITER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workpilot-bq")


def set_session(session_id: str) -> None:
    _SESSION.set(session_id or "")


def session_event_count(session_id: str) -> int:
    with _COUNT_LOCK:
        return _WRITTEN.get(session_id or "", 0)


def configured() -> bool:
    return not CONFIG.demo_mode and CONFIG.bigquery_enabled and bool(CONFIG.project_id)


def table_id() -> str:
    return f"{CONFIG.project_id}.{CONFIG.bigquery_dataset}.{CONFIG.bigquery_table}"


def _schema():
    from google.cloud import bigquery

    return [
        bigquery.SchemaField("event_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("event_type", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("workspace_id", "STRING"),
        bigquery.SchemaField("decision_id", "STRING"),
        bigquery.SchemaField("action_id", "STRING"),
        bigquery.SchemaField("source_count", "INTEGER"),
        bigquery.SchemaField("duration_ms", "INTEGER"),
        bigquery.SchemaField("estimated_minutes_saved", "FLOAT"),
        bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("metadata", "JSON"),
    ]


def _client():
    global _CLIENT
    if _CLIENT is None:
        from google.cloud import bigquery

        _CLIENT = bigquery.Client(project=CONFIG.project_id)
    return _CLIENT


def ensure_table():
    """Create dataset/table once per process. Returns the table or None."""
    global _TABLE_READY, _TABLE, _LAST_ERROR
    if not configured():
        return None
    from google.cloud import bigquery

    client = _client()
    if _TABLE_READY and _TABLE is not None:
        return _TABLE
    dataset = bigquery.Dataset(f"{CONFIG.project_id}.{CONFIG.bigquery_dataset}")
    dataset.location = CONFIG.bigquery_location
    client.create_dataset(dataset, exists_ok=True)
    table = client.create_table(bigquery.Table(table_id(), schema=_schema()), exists_ok=True)
    existing = {f.name for f in table.schema}
    if "event_id" not in existing:
        # Legacy table (event_time/workspace/payload_json). BigQuery only allows
        # adding NULLABLE columns, so migrate additively and keep writing the
        # legacy columns too (see log_metric).
        additions = [
            bigquery.SchemaField(f.name, f.field_type, mode="NULLABLE") for f in _schema() if f.name not in existing
        ]
        table.schema = list(table.schema) + additions
        table = client.update_table(table, ["schema"])
    _LEGACY_COLUMNS.clear()
    _LEGACY_COLUMNS.update({f.name for f in table.schema} & {"event_time", "workspace", "payload_json"})
    _TABLE = table
    _TABLE_READY = True
    return table


_LEGACY_COLUMNS: set = set()


def verify(force: bool = False) -> Tuple[bool, str]:
    global _VERIFIED
    if _VERIFIED is not None and not force:
        return _VERIFIED
    if not configured():
        _VERIFIED = (False, "not configured")
        return _VERIFIED
    try:
        table = ensure_table()
        _VERIFIED = (True, f"{table_id()} ({table.num_rows} rows)")
    except Exception as exc:
        _VERIFIED = (False, f"{type(exc).__name__}: {str(exc)[:160]}")
    return _VERIFIED


def status() -> Tuple[str, str]:
    """ACTIVE / NOT CONFIGURED / ERROR."""
    if CONFIG.demo_mode:
        return "NOT CONFIGURED", "DEMO_MODE: analytics disabled"
    if not CONFIG.bigquery_enabled:
        return "NOT CONFIGURED", "Set BIGQUERY_ENABLED=true"
    ok, detail = verify()
    if ok:
        return "ACTIVE", detail
    return "ERROR", f"BigQuery write path unavailable (workflow continues): {detail}"


def log_metric(
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    duration_ms: int = 0,
    decision_id: str = "",
    action_id: str = "",
    source_count: int = 0,
    estimated_minutes_saved: float = 0.0,
) -> bool:
    """Queue one event for BigQuery. Never raises.

    Returns True when the row was queued (the table is reachable). The insert
    runs on a background writer; only confirmed inserts are counted.
    """
    global _LAST_ERROR
    if not configured():
        return False
    try:
        ensure_table()
        session_id = _SESSION.get()
        meta = dict(payload or {})
        if session_id:
            meta["session_id"] = session_id
        row = {
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "workspace_id": CONFIG.workspace,
            "decision_id": decision_id or None,
            "action_id": action_id or None,
            "source_count": int(source_count),
            "duration_ms": int(duration_ms),
            "estimated_minutes_saved": float(estimated_minutes_saved),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": json.dumps(meta, default=str),
        }
        if "event_time" in _LEGACY_COLUMNS:
            row["event_time"] = row["timestamp"]
        if "workspace" in _LEGACY_COLUMNS:
            row["workspace"] = CONFIG.workspace
        if "payload_json" in _LEGACY_COLUMNS:
            row["payload_json"] = row["metadata"]
        _WRITER.submit(_insert, row, session_id)
        return True
    except Exception as exc:
        _LAST_ERROR = f"{type(exc).__name__}: {str(exc)[:200]}"
        log.warning("BigQuery write skipped: %s", exc)
        return False


def _insert(row: Dict[str, Any], session_id: str) -> bool:
    global _LAST_ERROR
    try:
        errors = _client().insert_rows_json(table_id(), [row])
    except Exception as exc:
        _LAST_ERROR = f"{type(exc).__name__}: {str(exc)[:200]}"
        log.warning("BigQuery insert failed (%s): %s", row.get("event_type"), exc)
        return False
    if errors:
        _LAST_ERROR = str(errors)[:200]
        log.warning("BigQuery insert errors: %s", errors)
        return False
    with _COUNT_LOCK:
        _WRITTEN[session_id] = _WRITTEN.get(session_id, 0) + 1
    return True


_COUNT_CACHE: Dict[str, Tuple[float, int]] = {}


def decision_event_count(decision_id: str, ttl_s: float = 30.0) -> Optional[int]:
    """Rows in the BigQuery events table for one decision, or None when BigQuery is off or the query fails.

    Streaming inserts can take a few seconds to become visible to queries.
    """
    import time

    if not configured() or not decision_id:
        return None
    hit = _COUNT_CACHE.get(decision_id)
    if hit and time.monotonic() - hit[0] < ttl_s:
        return hit[1]
    try:
        from google.cloud import bigquery

        job = _client().query(
            f"SELECT COUNT(*) AS n FROM `{table_id()}` WHERE decision_id = @d",
            job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("d", "STRING", decision_id)]),
        )
        n = int(next(iter(job.result())).n)
    except Exception as exc:
        log.warning("BigQuery count failed: %s", exc)
        return None
    _COUNT_CACHE[decision_id] = (time.monotonic(), n)
    return n
