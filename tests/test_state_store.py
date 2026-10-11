from workpilot import state_store
from workpilot.models import DecisionRecord, ExecutionRecord, WorkAction


def _action(title="Close findings", owner="Mei", **kw):
    fields = dict(title=title, owner=owner, due_date="12 Oct", priority="High", rationale="r", source="meeting_notes.md")
    fields.update(kw)
    return WorkAction(**fields).to_dict()


def test_backend_is_memory_in_demo_mode():
    label, detail = state_store.backend_status()
    assert label == "Demo"
    assert state_store.using_firestore() is False


def test_save_action_is_idempotent_by_action_id():
    first = _action()
    assert state_store.save_action(first) is False  # memory backend, not Firestore
    state_store.save_action(_action(priority="Critical"))
    actions = state_store.load_actions()
    assert len(actions) == 1
    assert actions[0]["priority"] == "Critical"


def test_save_action_never_downgrades_status():
    action = _action()
    state_store.save_action(action)
    ok, _ = state_store.approve_action(action["action_id"], "Sara")
    assert ok
    state_store.save_action(_action())  # regenerated as PROPOSED
    reloaded = state_store.get_action(action["action_id"])
    assert reloaded["status"] == "APPROVED"
    assert reloaded["approved_by"] == "Sara"


def test_load_actions_collapses_legacy_duplicates():
    # Simulate documents written by the previous app version: hash ids, legacy status, list source.
    col = state_store._col("actions")
    col.document("legacyhash1").set({"title": "Coordinate UAT refresh", "owner": "Ravi", "due_date": "7 Oct", "priority": "High", "rationale": "", "source": ["meeting_notes.md"], "status": "Proposed"})
    col.document("legacyhash2").set({"title": "Coordinate UAT refresh", "owner": "Ravi", "due_date": "7 Oct", "priority": "Medium", "rationale": "", "source": "meeting_notes.md", "status": "In Progress"})
    actions = state_store.load_actions()
    assert len(actions) == 1
    assert actions[0]["status"] == "IN_PROGRESS"
    assert actions[0]["source"] == "meeting_notes.md"
    assert actions[0]["action_id"].startswith("ACT-")


def test_transitions_enforced():
    action = _action()
    state_store.save_action(action)
    aid = action["action_id"]
    ok, msg = state_store.transition_action(aid, "COMPLETED")
    assert not ok and "PROPOSED" in msg
    assert state_store.approve_action(aid)[0]
    assert state_store.transition_action(aid, "IN_PROGRESS")[0]
    assert state_store.transition_action(aid, "COMPLETED")[0]
    assert state_store.transition_action(aid, "VERIFIED")[0]
    assert not state_store.transition_action(aid, "APPROVED")[0]
    assert state_store.transition_action("missing", "APPROVED") == (False, "Action missing not found")


def test_record_outcome_sets_lifecycle():
    action = _action()
    state_store.save_action(action)
    aid = action["action_id"]
    assert state_store.record_outcome(aid, "p95 = 1.32s", "ACHIEVED") == (False, "Approve this action before recording an outcome")
    state_store.approve_action(aid)
    assert state_store.record_outcome(aid, "p95 = 1.32s", "ACHIEVED") == (False, "Execute this action before recording an outcome")
    assert state_store.get_action(aid)["outcome_status"] == "PENDING"
    state_store.set_execution_status(aid, "EXECUTED")
    ok, status = state_store.record_outcome(aid, "p95 = 1.32s", "ACHIEVED")
    assert ok and status == "VERIFIED"
    assert state_store.get_action(aid)["actual_outcome"] == "p95 = 1.32s"
    ok, status = state_store.record_outcome(aid, "regressed", "NOT_ACHIEVED")
    assert status == "FAILED"
    assert state_store.record_outcome(aid, "x", "WEIRD") == (False, "Unknown outcome status WEIRD")


def test_decisions_and_links():
    rec = DecisionRecord(decision_id="DEC-1", workspace_id="ws", question="q", finding="f", recommendation="r", rationale="why")
    state_store.save_decision(rec)
    action = _action(decision_id="DEC-1")
    state_store.save_action(action)
    state_store.link_actions_to_decision("DEC-1", [action["action_id"]])
    loaded = state_store.get_decision("DEC-1")
    assert loaded.actions == [action["action_id"]]
    assert loaded.status == "ACTIONED"
    assert state_store.actions_for_decision("DEC-1")[0]["action_id"] == action["action_id"]
    assert state_store.get_decision("nope") is None
    assert [d.decision_id for d in state_store.load_decisions()] == ["DEC-1"]


def test_knowledge_sources_upsert():
    state_store.record_knowledge_source("s1", "a.md", 100, "Native", 1)
    state_store.record_knowledge_source("s1", "a.md", 120, "Native", 1)
    sources = state_store.load_knowledge_sources()
    assert len(sources) == 1 and sources[0]["word_count"] == 120


def test_contract_for_a_new_decision_never_inherits_lifecycle():
    """Legacy unscoped IDs can collide across decisions; state must not carry over."""
    prior = _action(decision_id="DEC-OLD")
    prior["action_id"] = "ACT-legacy"
    state_store.save_action(prior)
    state_store.approve_action("ACT-legacy")
    state_store.set_execution_status("ACT-legacy", "EXECUTED")
    state_store.record_outcome("ACT-legacy", "closed", "ACHIEVED")
    fresh = _action(decision_id="DEC-NEW")
    fresh["action_id"] = "ACT-legacy"
    state_store.save_action(fresh)
    stored = state_store.get_action("ACT-legacy")
    assert stored["status"] == "PROPOSED"
    assert stored["outcome_status"] == "PENDING"
    assert stored["execution_status"] == "NOT_STARTED"
    assert stored["approved_by"] == ""


def test_executions_and_events_ordering():
    state_store.save_execution(ExecutionRecord("EXEC-1", "ACT-1", "DEC-1", "gmail_draft", "a@b.c", "SUCCESS", created_at="2026-10-10T01:00:00+00:00"))
    state_store.save_execution(ExecutionRecord("EXEC-2", "ACT-1", "DEC-1", "calendar_followup", "cal", "FAILED", created_at="2026-10-10T02:00:00+00:00"))
    execs = state_store.load_executions()
    assert [e.execution_id for e in execs] == ["EXEC-2", "EXEC-1"]
    assert len(state_store.executions_for_action("ACT-1")) == 2
    state_store.log_event("a", {"n": 1})
    state_store.log_event("b", {"n": 2}, decision_id="DEC-1", action_id="ACT-1", estimated_minutes_saved=5)
    events = state_store.load_events(limit=1)
    assert events[0]["event_type"] == "b" and events[0]["estimated_minutes_saved"] == 5.0
