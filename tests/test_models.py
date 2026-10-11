from workpilot.models import (
    ActionStatus,
    DecisionRecord,
    ExecutionRecord,
    OutcomeStatus,
    WorkAction,
    make_action_id,
)


def test_work_action_normalizes_list_source_and_priority():
    action = WorkAction.from_llm(
        {"title": "Fix it", "owner": ["Mei"], "due_date": "12 Oct", "priority": "urgent",
         "rationale": "x", "source": ["meeting_notes.md", "issue_log.md"], "expected_outcome": "", "unknown_key": 1}
    )
    assert action.source == "meeting_notes.md, issue_log.md"
    assert action.evidence == ["meeting_notes.md", "issue_log.md"]
    assert action.owner == "Mei"
    assert action.priority == "Medium"  # clamped to vocabulary
    assert action.expected_outcome == "TBD"
    assert action.status == ActionStatus.PROPOSED
    assert action.action_id.startswith("ACT-")


def test_action_id_is_deterministic_and_case_insensitive():
    assert make_action_id("Close findings", "Mei") == make_action_id("close  FINDINGS", "mei")
    assert make_action_id("Close findings", "Mei") != make_action_id("Close findings", "Arjun")


def test_legacy_status_mapping():
    assert ActionStatus.normalize("Proposed") == "PROPOSED"
    assert ActionStatus.normalize("In Progress") == "IN_PROGRESS"
    assert ActionStatus.normalize("in progress") == "IN_PROGRESS"
    assert ActionStatus.normalize(None) == "PROPOSED"
    assert ActionStatus.normalize("garbage") == "PROPOSED"


def test_transitions():
    assert ActionStatus.can_transition("PROPOSED", "APPROVED")
    assert ActionStatus.can_transition("PROPOSED", "REJECTED")
    assert not ActionStatus.can_transition("REJECTED", "APPROVED")
    assert not ActionStatus.can_transition("PROPOSED", "COMPLETED")
    assert ActionStatus.can_transition("APPROVED", "IN_PROGRESS")
    assert ActionStatus.can_transition("COMPLETED", "VERIFIED")
    assert not ActionStatus.can_transition("VERIFIED", "PROPOSED")


def test_decision_record_round_trip():
    rec = DecisionRecord(
        decision_id="DEC-1", workspace_id="ws", question="q", finding=["a", "b"], recommendation="r",
        rationale="why", evidence=["meeting_notes.md", {"source": "x.md", "text": "excerpt"}],
    )
    data = rec.to_dict()
    assert data["finding"] == "a, b"
    assert data["evidence"][0] == {"source": "meeting_notes.md", "excerpt": ""}
    assert data["evidence"][1]["excerpt"] == "excerpt"
    clone = DecisionRecord.from_dict({**data, "unexpected": True})
    assert clone.decision_id == "DEC-1" and clone.status == "OPEN"


def test_execution_record_round_trip():
    rec = ExecutionRecord(execution_id="EXEC-1", action_id="ACT-1", decision_id="DEC-1", execution_type="gmail_draft", target="a@b.c", status="SUCCESS", mode="DEMO")
    clone = ExecutionRecord.from_dict(rec.to_dict())
    assert clone == rec
    assert OutcomeStatus.ACHIEVED in OutcomeStatus.ALL
