"""Follow-up execution after human approval.

    ExecutionAdapter
        └── GoogleWorkspaceAdapter  (REAL: Gmail draft + Calendar event via user OAuth)

Rules enforced here, not in the UI:
- An action must be APPROVED (or later) before anything executes.
- Gmail is draft-only. Nothing is ever sent automatically.
- Calendar events are created with sendUpdates="none" (no invitations go out).
- Every attempt, successful or not, produces an ExecutionRecord receipt.
- An action is marked EXECUTED only after the Google API call returns an ID.

Execution needs a user OAuth refresh token (scripts/authorize_workspace_oauth.py)
because the Cloud Run service account cannot act inside a consumer Gmail
mailbox. The token JSON is injected as WORKPILOT_OAUTH_TOKEN_JSON from Secret
Manager. Without it the adapter is "not connected" and nothing is executed.
"""
import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any, Dict, Optional, Protocol, Tuple

from . import state_store
from .config import CONFIG
from .models import (
    ActionStatus,
    DecisionRecord,
    ExecutionMode,
    ExecutionRecord,
    ExecutionStatus,
    make_execution_id,
)

log = logging.getLogger("workpilot.execution")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]

GMAIL_DRAFT = "gmail_draft"
CALENDAR_FOLLOWUP = "calendar_followup"

_CREDS = None
_CREDS_ERROR = ""
_VERIFIED: Optional[Tuple[bool, str]] = None


@dataclass
class ExecutionResult:
    external_reference: str
    target: str
    summary: str


class ExecutionAdapter(Protocol):
    mode: str

    def create_gmail_draft(self, to: str, subject: str, body: str) -> ExecutionResult: ...

    def create_calendar_followup(self, title: str, description: str, when: date, attendee: str) -> ExecutionResult: ...


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------
def _load_token_info() -> Optional[Dict[str, Any]]:
    raw = CONFIG.oauth_token_json
    if not raw and CONFIG.oauth_token_file:
        try:
            with open(CONFIG.oauth_token_file, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError:
            return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def credentials():
    """User OAuth credentials or None. Never raises."""
    global _CREDS, _CREDS_ERROR
    if _CREDS is not None:
        return _CREDS
    info = _load_token_info()
    if not info:
        _CREDS_ERROR = "WORKPILOT_OAUTH_TOKEN_JSON not set"
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(info, SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
        _CREDS = creds
        return creds
    except Exception as exc:
        _CREDS_ERROR = f"{type(exc).__name__}: {str(exc)[:160]}"
        log.warning("OAuth credentials unavailable: %s", exc)
        return None


def verify(force: bool = False) -> Tuple[bool, str]:
    """Confirm the token can reach Gmail and Calendar. Cached per process."""
    global _VERIFIED
    if _VERIFIED is not None and not force:
        return _VERIFIED
    creds = credentials()
    if creds is None:
        _VERIFIED = (False, _CREDS_ERROR)
        return _VERIFIED
    try:
        from googleapiclient.discovery import build

        gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = gmail.users().getProfile(userId="me").execute()
        calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
        # events.list is covered by the calendar.events scope; calendarList is not.
        events = calendar.events().list(calendarId="primary", maxResults=1).execute()
        _VERIFIED = (True, f"{profile.get('emailAddress')} · calendar '{events.get('summary', 'primary')}'")
    except Exception as exc:
        _VERIFIED = (False, f"{type(exc).__name__}: {str(exc)[:160]}")
    return _VERIFIED


def real_available() -> bool:
    return CONFIG.execution_enabled and not CONFIG.demo_mode and verify()[0]


NOT_CONNECTED = "Adapter not connected"


def status() -> Tuple[str, str]:
    """Execution status for the stack view: ACTIVE only after a live check."""
    if not CONFIG.execution_enabled:
        return "NOT CONNECTED", f"{NOT_CONNECTED}: EXECUTION_ENABLED=false"
    if CONFIG.demo_mode:
        return "NOT CONNECTED", f"{NOT_CONNECTED}: DEMO_MODE, no Gmail or Calendar calls"
    ok, detail = verify()
    if ok:
        return "ACTIVE", f"Gmail drafts + Calendar events as {detail}"
    return "NOT CONNECTED", f"{NOT_CONNECTED}: {detail}"


def not_connected_reason() -> str:
    label, detail = status()
    return "" if label == "ACTIVE" else detail


def gmail_status() -> Tuple[str, str]:
    label, detail = status()
    return label, detail.replace("Gmail drafts + Calendar events", "Gmail drafts")


def calendar_status() -> Tuple[str, str]:
    label, detail = status()
    return label, detail.replace("Gmail drafts + Calendar events", "Calendar events")


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------
class GoogleWorkspaceAdapter:
    mode = ExecutionMode.REAL

    def __init__(self, creds):
        from googleapiclient.discovery import build

        self._gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def create_gmail_draft(self, to: str, subject: str, body: str) -> ExecutionResult:
        message = EmailMessage()
        if to:  # a brief draft has no recipient; the user picks one before sending
            message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        draft = self._gmail.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        draft_id = draft.get("id", "")
        msg_id = (draft.get("message") or {}).get("id", "")
        return ExecutionResult(
            external_reference=f"gmail:draft/{draft_id}",
            target=to or "your Gmail drafts",
            summary=f"Gmail draft {draft_id} created (message {msg_id}); not sent.",
        )

    def create_calendar_followup(self, title: str, description: str, when: date, attendee: str) -> ExecutionResult:
        event_body: Dict[str, Any] = {
            "summary": title,
            "description": description,
            "start": {"date": when.isoformat()},
            "end": {"date": (when + timedelta(days=1)).isoformat()},
            "reminders": {"useDefault": True},
        }
        if attendee and _looks_like_email(attendee):
            event_body["attendees"] = [{"email": attendee}]
        event = (
            self._calendar.events()
            .insert(calendarId="primary", body=event_body, sendUpdates="none")
            .execute()
        )
        return ExecutionResult(
            external_reference=event.get("htmlLink") or f"calendar:event/{event.get('id', '')}",
            target=f"primary calendar · {when.isoformat()}",
            summary=f"Calendar follow-up '{title}' on {when.isoformat()} (no invitations sent).",
        )


def get_adapter() -> Optional[ExecutionAdapter]:
    """The live Google Workspace adapter, or None when OAuth is not connected."""
    if not CONFIG.execution_enabled or CONFIG.demo_mode:
        return None
    creds = credentials()
    if creds is None or not verify()[0]:
        return None
    try:
        return GoogleWorkspaceAdapter(creds)
    except Exception as exc:
        log.warning("Workspace adapter init failed: %s", exc)
        return None


# --------------------------------------------------------------------------
# Composition helpers
# --------------------------------------------------------------------------
def _looks_like_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value or ""))


def owner_email(owner: str) -> str:
    owner = (owner or "").strip()
    if _looks_like_email(owner):
        return owner
    mapping = {}
    for pair in re.split(r"[;,]", CONFIG.owner_email_map):
        if "=" in pair:
            key, value = pair.split("=", 1)
            mapping[key.strip().lower()] = value.strip()
    if owner.lower() in mapping:
        return mapping[owner.lower()]
    slug = re.sub(r"[^a-z0-9.]+", ".", owner.lower()).strip(".") or "owner"
    return f"{slug}@{CONFIG.default_owner_email_domain}"


def parse_due_date(value: str, default_days: int = 1) -> date:
    """Best-effort date parsing; falls back to tomorrow when unparseable."""
    text = (value or "").strip()
    if text and text.upper() != "TBD":
        try:
            return date.fromisoformat(text[:10])  # ISO first: "2026-10-07"
        except ValueError:
            pass
        try:
            import pandas as pd

            parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
            if parsed is not None and not pd.isna(parsed):
                return parsed.date()
        except Exception:
            pass
    return date.today() + timedelta(days=default_days)


def _cited_evidence(evidence, decision: Optional[DecisionRecord]) -> str:
    excerpts = {}
    for item in (decision.evidence if decision is not None else []) or []:
        source = str(item.get("source") or "")
        if source and source not in excerpts and item.get("excerpt"):
            excerpts[source] = " ".join(str(item["excerpt"]).split())[:240]
    lines = []
    for name in evidence:
        quote = excerpts.get(name)
        lines.append(f"  - {name}: \"{quote}\"" if quote else f"  - {name}")
    return "\n".join(lines) or "  - (no evidence recorded)"


def compose_followup(action: Dict[str, Any], decision: Optional[DecisionRecord]) -> Tuple[str, str]:
    subject = f"WorkPilot Action — {action.get('title', '')}"
    evidence = action.get("evidence") or ([action.get("source")] if action.get("source") else [])
    evidence_lines = _cited_evidence(evidence, decision)
    decision_lines = ""
    if decision is not None:
        decision_lines = (
            f"\nDecision context ({decision.decision_id}):\n"
            f"  Question: {decision.question}\n"
            f"  Finding: {decision.finding[:500]}\n"
        )
    body = (
        f"Hello {action.get('owner', 'there')},\n\n"
        f"WorkPilot AI prepared this follow-up after human approval.\n\n"
        f"Action: {action.get('title', '')}\n"
        f"Objective: {action.get('expected_outcome', 'TBD')}\n"
        f"Due date: {action.get('due_date', 'TBD')}\n"
        f"Priority: {action.get('priority', 'Medium')}\n\n"
        f"Rationale:\n  {action.get('rationale', '')}\n\n"
        f"Cited evidence:\n{evidence_lines}\n"
        f"{decision_lines}\n"
        f"Approved by: {action.get('approved_by') or 'human reviewer'}\n"
        f"Action ID: {action.get('action_id', '')}\n\n"
        f"— WorkPilot AI · Know → Decide → Approve → Act → Measure\n"
    )
    return subject, body


# --------------------------------------------------------------------------
# Entry point used by the UI
# --------------------------------------------------------------------------
class ApprovalRequired(RuntimeError):
    """Raised when execution is attempted on an unapproved action."""


def execute_followup(action: Dict[str, Any], execution_type: str, decision: Optional[DecisionRecord] = None) -> ExecutionRecord:
    """Execute an approved follow-up and persist the receipt. Never raises for adapter errors."""
    status_value = ActionStatus.normalize(action.get("status"))
    if status_value == ActionStatus.PROPOSED:
        raise ApprovalRequired("Human approval is required before any follow-up is executed.")
    if status_value == ActionStatus.REJECTED:
        raise ApprovalRequired("Rejected actions cannot be executed.")
    if execution_type not in (GMAIL_DRAFT, CALENDAR_FOLLOWUP):
        raise ValueError(f"Unknown execution type: {execution_type}")
    if action.get("execution_status") == ExecutionStatus.EXECUTED:
        raise ValueError("This action was already executed.")

    adapter = get_adapter()
    record = ExecutionRecord(
        execution_id=make_execution_id(),
        action_id=action.get("action_id", ""),
        decision_id=action.get("decision_id", "") or (decision.decision_id if decision else ""),
        execution_type=execution_type,
        target="",
        status=ExecutionStatus.UNAVAILABLE,
        mode=getattr(adapter, "mode", ExecutionMode.REAL),
    )
    if adapter is None:
        # Nothing ran: the action's execution status stays Not started.
        record.error = not_connected_reason() or NOT_CONNECTED
        state_store.save_execution(record)
        return record

    try:
        subject, body = compose_followup(action, decision)
        to = owner_email(action.get("owner", ""))
        if execution_type == GMAIL_DRAFT:
            result = adapter.create_gmail_draft(to, subject, body)
        else:
            when = parse_due_date(action.get("due_date", ""))
            result = adapter.create_calendar_followup(subject, body, when, to)
        record.status = ExecutionStatus.SUCCESS
        record.target = result.target
        record.external_reference = result.external_reference
        record.summary = result.summary
    except Exception as exc:
        record.status = ExecutionStatus.FAILED
        record.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        log.warning("Execution failed (%s): %s", execution_type, exc)

    state_store.save_execution(record)
    if record.status == ExecutionStatus.SUCCESS:
        state_store.set_execution_status(record.action_id, ExecutionStatus.EXECUTED)
        state_store.transition_action(record.action_id, ActionStatus.IN_PROGRESS)
    else:
        state_store.set_execution_status(record.action_id, ExecutionStatus.FAILED)
    return record
