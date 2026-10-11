import base64
from datetime import date, timedelta

import pytest

from workpilot import execution, state_store
from workpilot.execution import (
    CALENDAR_FOLLOWUP,
    GMAIL_DRAFT,
    ApprovalRequired,
    GoogleWorkspaceAdapter,
    compose_followup,
    execute_followup,
    owner_email,
    parse_due_date,
)
from workpilot.models import DecisionRecord, WorkAction


def _approved_action():
    action = WorkAction(title="Complete security validation", owner="Mei", due_date="12 Oct 2026", priority="High",
                        rationale="Required before production", source="meeting_notes.md", expected_outcome="Findings closed").to_dict()
    state_store.save_action(action)
    state_store.approve_action(action["action_id"], "Sara")
    return state_store.get_action(action["action_id"])


def test_execution_requires_approval():
    proposed = WorkAction(title="x", owner="y", due_date="TBD", priority="Low", rationale="", source="").to_dict()
    with pytest.raises(ApprovalRequired):
        execute_followup(proposed, GMAIL_DRAFT)


def test_rejected_action_cannot_be_executed():
    action = WorkAction(title="x", owner="y", due_date="TBD", priority="Low", rationale="", source="").to_dict()
    state_store.save_action(action)
    state_store.reject_action(action["action_id"])
    rejected = state_store.get_action(action["action_id"])
    with pytest.raises(ApprovalRequired):
        execute_followup(rejected, GMAIL_DRAFT)


def test_calendar_followup_uses_due_date(live_adapter):
    action = _approved_action()
    record = execute_followup(action, CALENDAR_FOLLOWUP)
    assert record.status == "SUCCESS" and "2026-10-12" in record.target
    assert record.external_reference.startswith("https://calendar.google.com/")


def test_executed_action_cannot_run_twice(live_adapter):
    action = _approved_action()
    execute_followup(action, GMAIL_DRAFT)
    with pytest.raises(ValueError):
        execute_followup(state_store.get_action(action["action_id"]), GMAIL_DRAFT)
    assert len(live_adapter.calls) == 1


def test_unknown_execution_type_rejected():
    with pytest.raises(ValueError):
        execute_followup(_approved_action(), "sms")


def test_adapter_failure_produces_failed_receipt(monkeypatch):
    class Broken:
        mode = "REAL"

        def create_gmail_draft(self, *a, **k):
            raise RuntimeError("insufficient scopes")

    monkeypatch.setattr(execution, "get_adapter", lambda: Broken())
    action = _approved_action()
    record = execute_followup(action, GMAIL_DRAFT)
    assert record.status == "FAILED" and record.mode == "REAL" and "insufficient scopes" in record.error
    updated = state_store.get_action(action["action_id"])
    assert updated["status"] == "APPROVED"  # not advanced
    assert updated["execution_status"] == "FAILED"


def test_no_adapter_keeps_action_not_started():
    action = _approved_action()
    record = execute_followup(action, GMAIL_DRAFT)
    assert record.status == "UNAVAILABLE" and "not connected" in record.error.lower()
    updated = state_store.get_action(action["action_id"])
    assert updated["execution_status"] == "NOT_STARTED"
    assert updated["status"] == "APPROVED"


def test_compose_followup_contains_required_fields():
    action = _approved_action()
    decision = DecisionRecord(decision_id="DEC-1", workspace_id="ws", question="What blocks release?", finding="Security findings open", recommendation="r", rationale="x")
    subject, body = compose_followup(action, decision)
    assert subject == "WorkPilot Action — Complete security validation"
    for needle in ("Objective: Findings closed", "Due date: 12 Oct 2026", "Rationale", "Cited evidence", "meeting_notes.md", "DEC-1", "Approved by: Sara"):
        assert needle in body


def test_compose_followup_quotes_cited_evidence():
    action = _approved_action()
    decision = DecisionRecord(
        decision_id="DEC-2", workspace_id="ws", question="q", finding="f", recommendation="r", rationale="x",
        evidence=[{"source": "meeting_notes.md", "excerpt": "SEC-217 must be validated before production approval."}],
    )
    _, body = compose_followup(action, decision)
    assert 'meeting_notes.md: "SEC-217 must be validated before production approval."' in body


def test_owner_email_mapping(set_config):
    assert owner_email("mei@corp.com") == "mei@corp.com"
    assert owner_email("Mei") == "mei@example.com"
    set_config(owner_email_map="Mei=mei.chen@corp.com", default_owner_email_domain="corp.com")
    assert owner_email("mei") == "mei.chen@corp.com"
    assert owner_email("Arjun Rao") == "arjun.rao@corp.com"


def test_parse_due_date():
    assert parse_due_date("12 Oct 2026") == date(2026, 10, 12)
    assert parse_due_date("2026-10-07") == date(2026, 10, 7)
    assert parse_due_date("TBD") == date.today() + timedelta(days=1)
    assert parse_due_date("") == date.today() + timedelta(days=1)


class _FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeDrafts:
    def __init__(self, sink):
        self.sink = sink

    def create(self, userId, body):
        self.sink["body"] = body
        return _FakeExecute({"id": "d123", "message": {"id": "m456"}})


class _FakeGmail:
    def __init__(self, sink):
        self.sink = sink

    def users(self):
        return self

    def drafts(self):
        return _FakeDrafts(self.sink)


class _FakeEvents:
    def __init__(self, sink):
        self.sink = sink

    def insert(self, calendarId, body, sendUpdates):
        self.sink.update({"calendarId": calendarId, "body": body, "sendUpdates": sendUpdates})
        return _FakeExecute({"id": "ev1", "htmlLink": "https://calendar.google.com/event?eid=ev1"})


class _FakeCalendar:
    def __init__(self, sink):
        self.sink = sink

    def events(self):
        return _FakeEvents(self.sink)


def test_real_adapter_builds_draft_and_event_payloads(monkeypatch):
    gmail_sink, cal_sink = {}, {}
    adapter = GoogleWorkspaceAdapter.__new__(GoogleWorkspaceAdapter)
    adapter._gmail = _FakeGmail(gmail_sink)
    adapter._calendar = _FakeCalendar(cal_sink)

    result = adapter.create_gmail_draft("mei@example.com", "WorkPilot Action — X", "body text")
    raw = base64.urlsafe_b64decode(gmail_sink["body"]["message"]["raw"]).decode()
    assert "Subject: WorkPilot Action" in raw and "To: mei@example.com" in raw and "body text" in raw
    assert result.external_reference == "gmail:draft/d123" and "not sent" in result.summary

    result = adapter.create_calendar_followup("Follow-up", "desc", date(2026, 10, 12), "mei@example.com")
    assert cal_sink["sendUpdates"] == "none"  # never emails invitees
    assert cal_sink["body"]["start"] == {"date": "2026-10-12"} and cal_sink["body"]["end"] == {"date": "2026-10-13"}
    assert cal_sink["body"]["attendees"] == [{"email": "mei@example.com"}]
    assert result.external_reference.startswith("https://calendar.google.com/")


def test_status_is_not_connected_without_token():
    label, detail = execution.status()
    assert label == "NOT CONNECTED"
    assert execution.get_adapter() is None


def test_gmail_draft_marks_executed_only_after_api_returns(live_adapter):
    action = _approved_action()
    record = execute_followup(action, GMAIL_DRAFT)
    assert record.status == "SUCCESS" and record.mode == "REAL"
    assert record.target == "mei@example.com"
    assert record.external_reference == "gmail:draft/r-123"
    stored = state_store.executions_for_action(action["action_id"])
    assert stored and stored[0].execution_id == record.execution_id
    updated = state_store.get_action(action["action_id"])
    assert updated["execution_status"] == "EXECUTED"
    assert updated["status"] == "IN_PROGRESS"
