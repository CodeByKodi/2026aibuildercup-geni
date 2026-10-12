from workpilot import state_store
from workpilot.config import ImpactAssumptions
from workpilot.impact import compute_impact, methodology_markdown
from workpilot.models import DecisionRecord, ExecutionRecord, WorkAction


def test_zero_state_gives_zero_metrics():
    impact = compute_impact()
    m = impact["metrics"]
    assert m["knowledge_sources_analyzed"] == 0
    assert m["estimated_weekly_time_saved_hours"] == 0
    assert m["estimated_total_time_saved_hours"] == 0
    assert m["release_blockers_consolidated"] == 0
    assert m["followups_automated"] == 0
    assert m["steps_removed"] == 0
    assert impact["per_task"] == []


def test_metrics_are_derived_from_records_and_assumptions():
    state_store.record_knowledge_source("s1", "a.md", 6000, "Native", 1)
    state_store.record_knowledge_source("s2", "b.md", 6000, "Native", 1)
    for i in range(3):
        state_store.save_action(WorkAction(title=f"A{i}", owner="Mei", due_date="TBD", priority="High", rationale="", source="a.md").to_dict())
    state_store.save_decision(DecisionRecord(decision_id="DEC-1", workspace_id="ws", question="q", finding="f", recommendation="r", rationale="x"))
    state_store.save_execution(ExecutionRecord("EXEC-1", "ACT-1", "DEC-1", "gmail_draft", "a@b.c", "SUCCESS", mode="REAL"))
    state_store.save_execution(ExecutionRecord("EXEC-2", "ACT-2", "DEC-1", "calendar_followup", "cal", "SUCCESS", mode="DEMO"))
    state_store.save_execution(ExecutionRecord("EXEC-3", "ACT-3", "DEC-1", "gmail_draft", "x", "FAILED", mode="DEMO"))
    state_store.log_event("action_identified", {"count": 5})
    state_store.log_event("blocker_identified", {"count": 3})

    assumptions = ImpactAssumptions(
        reading_words_per_minute=200,
        minutes_per_prepared_action=5,
        minutes_per_followup=5,
        minutes_per_decision=10,
        minutes_per_blocker=10,
        minutes_per_owner_coordination=15,
        window_days=7,
    )
    impact = compute_impact(assumptions)
    m = impact["metrics"]
    assert m["knowledge_sources_analyzed"] == 2
    assert m["estimated_manual_reading_avoided_hours"] == 1.0  # 12000 words / 200 wpm = 60 min
    assert m["potential_actions_identified"] == 5
    assert m["actions_automatically_prepared"] == 3
    assert m["decisions_supported"] == 1
    assert m["release_blockers_consolidated"] == 3
    assert m["followups_automated"] == 2
    assert m["followups_real"] == 1 and m["followups_demo"] == 1
    # 60 reading + 25 actions (5 identified) + 10 decision + 30 blockers + 15 coordination (1 owner)
    assert m["estimated_total_time_saved_hours"] == round(140 / 60, 2)
    assert m["steps_removed"] >= 3
    assert len(impact["per_task"]) == 3
    assert impact["per_task"][0]["estimated_minutes"] == 5
    assert m["estimated_weekly_time_saved_hours"] == round(140 / 60, 2)
    assert impact["breakdown_minutes"]["reading"] == 60.0
    assert impact["breakdown_minutes"]["action_preparation"] == 25.0
    assert impact["breakdown_minutes"]["decision_summarization"] == 10.0
    assert impact["breakdown_minutes"]["blocker_analysis"] == 30.0
    assert impact["breakdown_minutes"]["coordination"] == 15.0
    assert impact["assumptions"]["reading_words_per_minute"] == 200


def test_metrics_can_be_scoped_to_one_decision():
    state_store.save_action(
        WorkAction(title="This", owner="Mei", due_date="TBD", priority="High", rationale="", source="a.md", decision_id="DEC-LIVE").to_dict()
    )
    state_store.save_action(
        WorkAction(title="Other", owner="Arjun", due_date="TBD", priority="Low", rationale="", source="b.md", decision_id="DEC-OLD").to_dict()
    )
    state_store.save_decision(DecisionRecord(decision_id="DEC-LIVE", workspace_id="ws", question="q", finding="f", recommendation="r", rationale="x"))
    state_store.save_decision(DecisionRecord(decision_id="DEC-OLD", workspace_id="ws", question="old", finding="f", recommendation="r", rationale="x"))
    scoped = compute_impact(decision_id="DEC-LIVE")
    assert scoped["scope"] == "decision"
    assert scoped["metrics"]["decisions_supported"] == 1
    assert scoped["metrics"]["actions_automatically_prepared"] == 1


def test_documents_override_historical_knowledge_sources():
    state_store.record_knowledge_source("old1", "project_status.md", 4000, "Native", 1)
    state_store.record_knowledge_source("old2", "meeting_notes.md", 4000, "Native", 1)
    state_store.record_knowledge_source("s1", "security_assessment.md", 200, "Native", 1)
    current = {
        "security_assessment.md": "SEC-217 token lifetime " * 20,
        "performance_test_report.md": "p95 2.4 seconds " * 20,
    }
    impact = compute_impact(documents=current)
    assert impact["metrics"]["knowledge_sources_analyzed"] == 2
    assert impact["inputs"]["total_words_analyzed"] == sum(len(text.split()) for text in current.values())


def test_assumptions_change_the_estimate_transparently():
    state_store.record_knowledge_source("s1", "a.md", 6000, "Native", 1)
    fast = compute_impact(ImpactAssumptions(reading_words_per_minute=400))["metrics"]["estimated_manual_reading_avoided_hours"]
    slow = compute_impact(ImpactAssumptions(reading_words_per_minute=100))["metrics"]["estimated_manual_reading_avoided_hours"]
    assert (fast, slow) == (0.25, 1.0)
    text = methodology_markdown(ImpactAssumptions(reading_words_per_minute=250))
    assert "250 words per minute" in text and "Manual baseline (formula estimate)" in text and "manual baseline − WorkPilot run" in text




def test_baseline_is_fixed_when_the_decision_is_recorded():
    """The analysis names the follow-ups and owners, so preparing contracts later does not move the baseline."""
    state_store.log_event("action_identified", {"count": 3, "owners": 3}, decision_id="DEC-B")
    state_store.log_event("blocker_identified", {"count": 3}, decision_id="DEC-B")
    state_store.save_decision(DecisionRecord(decision_id="DEC-B", workspace_id="ws", question="q", finding="f", recommendation="r", rationale="x"))
    before = compute_impact(decision_id="DEC-B", documents={})["metrics"]["estimated_total_time_saved_hours"]
    for i, owner in enumerate(["Mei", "Arjun", "Lena"]):
        state_store.save_action(
            WorkAction(title=f"A{i}", owner=owner, due_date="TBD", priority="High", rationale="", source="a.md", decision_id="DEC-B").to_dict()
        )
    after = compute_impact(decision_id="DEC-B", documents={})["metrics"]["estimated_total_time_saved_hours"]
    assert before == after > 0
