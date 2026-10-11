from workpilot import scenario, state_store, workflow
from workpilot.models import ActionStatus, DecisionRecord, OutcomeStatus, WorkAction


def test_session_roundtrip_restores_step_and_analysis():
    sid = workflow.new_session_id()
    assert sid.startswith("SES-")
    saved = state_store.save_session(
        {
            "session_id": sid,
            "current_step": "Decide",
            "question": scenario.QUESTION,
            "decision_id": "DEC-1",
            "selected_action_ids": ["ACT-1"],
            "action_contracts": [{"action_id": "ACT-1", "title": "Validate SEC-217", "decision_id": "DEC-1"}],
            "analysis": {"decision_id": "DEC-1", "decision": {"finding": "Not ready"}, "hits": []},
        }
    )
    assert saved is False  # memory backend in tests
    loaded = state_store.get_session(sid)
    assert loaded["current_step"] == "Decide"
    assert loaded["decision_id"] == "DEC-1"
    assert loaded["analysis"]["decision"]["finding"] == "Not ready"
    assert loaded["action_contracts"][0]["action_id"] == "ACT-1"
    assert state_store.get_session("missing") is None


def test_new_session_does_not_delete_decision_memory():
    rec = DecisionRecord(
        decision_id="DEC-KEEP",
        workspace_id="ws",
        question="q",
        finding="f",
        recommendation="r",
        rationale="x",
    )
    state_store.save_decision(rec)
    state_store.save_session({"session_id": "SES-old", "current_step": "Approve", "decision_id": "DEC-KEEP"})
    state_store.save_session({"session_id": "SES-new", "current_step": "Know", "decision_id": ""})
    assert state_store.get_decision("DEC-KEEP") is not None
    assert state_store.get_session("SES-old")["current_step"] == "Approve"


def test_sample_scenario_has_six_enterprise_sources(sample_docs):
    for name in scenario.SOURCE_FILES:
        assert name in sample_docs
        assert len(sample_docs[name].split()) > 40
    assert len(scenario.SOURCE_FILES) == 6
    assert "go live" in scenario.QUESTION.lower()


def test_readiness_changes_only_after_outcomes():
    actions = [
        WorkAction(
            title="Validate SEC-217 remediation",
            owner="Mei",
            due_date="12 Oct",
            priority="High",
            rationale="SEC-217",
            source="security_assessment.md",
            expected_outcome="closed",
            status=ActionStatus.APPROVED,
        ).to_dict()
    ]
    before = scenario.readiness_from_actions(actions)
    assert before["status"] == "NOT READY"
    actions[0]["outcome_status"] = OutcomeStatus.ACHIEVED
    actions[0]["status"] = ActionStatus.VERIFIED
    actions[0]["actual_outcome"] = "SEC-217 verified closed"
    after = scenario.readiness_from_actions(actions)
    assert after["status"] == "READY FOR APPROVAL"
    assert "human" in after["summary"].lower()
    gated = actions + [
        WorkAction(
            title="Production Go-Live",
            owner="Release Manager",
            due_date="18 Oct",
            priority="Critical",
            rationale="Human gate",
            source="release_status_report.md",
            expected_outcome="Human approval",
        ).to_dict()
    ]
    assert scenario.is_human_gate_action(gated[-1]) is True
    assert scenario.readiness_from_actions(gated)["status"] == "READY FOR APPROVAL"
    remediation = WorkAction(
        title="Validate SEC-217 remediation",
        owner="Mei",
        due_date="12 Oct",
        priority="High",
        rationale="SEC-217",
        source="security_assessment.md",
        expected_outcome="SEC-217 closed before production go-live",
        status=ActionStatus.APPROVED,
    ).to_dict()
    assert scenario.is_human_gate_action(remediation) is False
    assert scenario.readiness_from_actions([remediation])["status"] == "NOT READY"


def test_confirmed_outcomes_move_readiness_and_only_after_execution():
    rec = DecisionRecord(
        decision_id="DEC-READY",
        workspace_id="ws",
        question=scenario.QUESTION,
        finding="Not ready",
        recommendation="Close blockers",
        rationale="Three conditions",
    )
    state_store.save_decision(rec)
    for title, owner, source in (
        ("Validate SEC-217 remediation", "Mei", "security_assessment.md"),
        ("Deploy cache and rerun performance test", "Arjun", "performance_test_report.md"),
        ("Retest PAY-482 and complete UAT sign-off", "Priya", "uat_meeting_notes.md"),
    ):
        action = WorkAction(
            title=title,
            owner=owner,
            due_date="TBD",
            priority="High",
            rationale=title,
            source=source,
            expected_outcome="done",
            decision_id="DEC-READY",
            status=ActionStatus.APPROVED,
        ).to_dict()
        state_store.save_action(action)
        state_store.link_actions_to_decision("DEC-READY", [action["action_id"]])
    board = state_store.actions_for_decision("DEC-READY")
    ok, _ = workflow.verify_outcome(board[0]["action_id"], "SEC-217 closed", OutcomeStatus.ACHIEVED)
    assert ok is False  # approved but never executed: no outcome may be recorded
    assert state_store.get_decision("DEC-READY").actual_outcome == ""
    for action in board:
        state_store.set_execution_status(action["action_id"], "EXECUTED")
        ok, _ = workflow.verify_outcome(action["action_id"], "What the owner reported", OutcomeStatus.ACHIEVED, verified_by="Maya")
        assert ok
    board = state_store.actions_for_decision("DEC-READY")
    assert all(a["outcome_status"] == OutcomeStatus.ACHIEVED for a in board)
    assert scenario.readiness_from_actions(board)["status"] == "READY FOR APPROVAL"
    events = [e for e in state_store.load_events(limit=50) if e["event_type"] == "release_readiness_updated"]
    assert len(events) == 3 and events[0]["payload"]["verified_by"] == "Maya"


def test_no_scripted_outcomes_exist():
    assert not hasattr(workflow, "apply_demo_outcomes")
    assert not hasattr(scenario, "DEMO_OUTCOMES")
