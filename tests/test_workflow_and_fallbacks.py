import pytest

from workpilot import adk_orchestrator, analytics, execution, state_store, workflow
from workpilot.cloud_status import technology_status
from workpilot.retrieval import EnterpriseRetriever


@pytest.fixture
def retriever(sample_docs):
    r = EnterpriseRetriever()
    r.build(sample_docs)
    return r


def test_second_generation_does_not_duplicate_or_downgrade(retriever):
    analysis = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    board, _ = workflow.generate_contracts(retriever, analysis["decision_record"], analysis["decision"])
    aid = board[0]["action_id"]
    workflow.approve_and_execute(aid, None)
    board2, _ = workflow.generate_contracts(retriever, analysis["decision_record"], analysis["decision"])
    assert len(board2) == len(board)
    assert next(a for a in board2 if a["action_id"] == aid)["status"] == "APPROVED"


def test_reject_does_not_execute_or_approve(retriever):
    analysis = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    board, _ = workflow.generate_contracts(retriever, analysis["decision_record"], analysis["decision"])
    aid = board[0]["action_id"]
    out = workflow.reject_action(aid, rejected_by="Judge")
    assert out["ok"] and out["action"]["status"] == "REJECTED"
    assert out["action"].get("execution_status") in ("NOT_STARTED", "", None) or out["action"]["execution_status"] == "NOT_STARTED"
    blocked = workflow.approve_and_execute(aid, None)
    assert blocked["ok"] is False
    assert blocked["action"]["status"] == "REJECTED"


def test_approve_without_execution_and_double_approval(retriever):
    analysis = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    board, _ = workflow.generate_contracts(retriever, analysis["decision_record"])
    aid = board[0]["action_id"]
    first = workflow.approve_and_execute(aid, None)
    assert first["ok"] and first["execution"] is None and first["action"]["status"] == "APPROVED"
    second = workflow.approve_and_execute(aid, execution.CALENDAR_FOLLOWUP)
    assert second["execution"].status == "UNAVAILABLE"  # no OAuth adapter connected
    assert second["action"]["execution_status"] == "NOT_STARTED"
    assert workflow.approve_and_execute("ACT-missing", None)["ok"] is False


def test_ingest_upload_registers_source():
    text, meta = workflow.ingest_upload("ops.md", b"one two three four")
    assert text == "one two three four" and meta["word_count"] == 4
    assert state_store.load_knowledge_sources()[0]["name"] == "ops.md"


def test_bigquery_is_skipped_not_fatal_when_unconfigured():
    assert analytics.configured() is False
    assert analytics.log_metric("knowledge_analyzed", {"x": 1}) is False
    assert analytics.status()[0] == "NOT CONFIGURED"


def test_bigquery_errors_never_raise(set_config, monkeypatch):
    set_config(demo_mode=False, bigquery_enabled=True, project_id="p")

    def boom():
        raise RuntimeError("no permission")

    monkeypatch.setattr(analytics, "ensure_table", boom)
    assert analytics.log_metric("x", {}) is False


def test_adk_fallback_status_in_demo():
    assert adk_orchestrator.configured() is False
    assert adk_orchestrator.status()[0] == "FALLBACK"
    with pytest.raises(RuntimeError):
        adk_orchestrator.run_turn("instruction", "q", [])
    from workpilot.agent import orchestrator

    assert orchestrator.run_decide("q", EnterpriseRetriever(), {}, "SES-x") is None  # caller runs the pipeline


def test_firestore_outage_in_audit_logging_does_not_break_flow(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(state_store, "log_event", boom)
    workflow._emit("knowledge_analyzed", {"ok": True})  # must not raise


def test_technology_status_is_truthful_in_demo_mode():
    rows = {r["technology"]: r["status"] for r in technology_status()}
    assert rows["Gemini on Vertex AI"] == "FALLBACK"
    assert rows["Vertex AI Search / Agent Search"] == "FALLBACK"
    assert rows["Document AI"] == "FALLBACK"
    assert rows["BigQuery"] == "NOT CONFIGURED"
    assert rows["Gmail (drafts only)"] == "NOT CONNECTED"
    assert rows["Cloud Run"] == "LOCAL"
    assert "ACTIVE" not in rows.values()


def test_new_session_contracts_start_proposed_even_if_identical_contract_was_approved(retriever, live_adapter):
    """P0 regression: SEC-217 appeared Approved and ACHIEVED in a fresh session."""
    first = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    board, _ = workflow.generate_contracts(retriever, first["decision_record"], first["decision"])
    for action in board:
        workflow.approve_and_execute(action["action_id"], None)
        workflow.execute_action(action["action_id"], execution.GMAIL_DRAFT)
        workflow.verify_outcome(action["action_id"], "Verified by the owner", "ACHIEVED", verified_by="Maya")
    assert all(a["outcome_status"] == "ACHIEVED" for a in state_store.actions_for_decision(first["decision_record"].decision_id))

    second = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    workflow.generate_contracts(retriever, second["decision_record"], second["decision"])
    fresh = state_store.actions_for_decision(second["decision_record"].decision_id)
    assert fresh and {a["title"] for a in fresh} == {a["title"] for a in board}
    assert not {a["action_id"] for a in fresh} & {a["action_id"] for a in board}
    for action in fresh:
        assert action["status"] == "PROPOSED"
        assert action["execution_status"] == "NOT_STARTED"
        assert action["outcome_status"] == "PENDING"
        assert action["approved_by"] == ""


def test_approval_is_an_audited_click_and_nothing_else_approves(retriever):
    analysis = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    board, _ = workflow.generate_contracts(retriever, analysis["decision_record"], analysis["decision"])
    assert all(a["status"] == "PROPOSED" for a in board)
    target = board[0]["action_id"]
    workflow.approve_and_execute(target, None, approved_by="Maya Chen")
    approvals = [e for e in state_store.load_events(limit=200) if e["event_type"] == "action_approved"]
    assert [e["action_id"] for e in approvals] == [target]
    assert approvals[0]["created_at"] and approvals[0]["payload"]["approved_by"] == "Maya Chen"
    assert state_store.get_action(target)["approved_at"]
    others = [a for a in state_store.actions_for_decision(analysis["decision_record"].decision_id) if a["action_id"] != target]
    assert others and all(a["status"] == "PROPOSED" for a in others)


def test_outcome_requires_approval_then_execution(retriever, live_adapter):
    analysis = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    board, _ = workflow.generate_contracts(retriever, analysis["decision_record"], analysis["decision"])
    aid = board[0]["action_id"]
    assert workflow.verify_outcome(aid, "done", "ACHIEVED")[0] is False
    workflow.approve_and_execute(aid, None)
    assert workflow.verify_outcome(aid, "done", "ACHIEVED")[0] is False
    assert state_store.get_decision(analysis["decision_record"].decision_id).actual_outcome == ""
    workflow.execute_action(aid, execution.CALENDAR_FOLLOWUP)
    assert workflow.verify_outcome(aid, "done", "ACHIEVED") == (True, "VERIFIED")
    events = [e for e in state_store.load_events(limit=200) if e["event_type"] == "outcome_verified"]
    assert len(events) == 1 and events[0]["action_id"] == aid and events[0]["created_at"]


def test_execute_without_adapter_stays_not_started_and_is_audited(retriever):
    analysis = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    board, _ = workflow.generate_contracts(retriever, analysis["decision_record"], analysis["decision"])
    aid = board[0]["action_id"]
    assert workflow.execute_action(aid, execution.GMAIL_DRAFT)["ok"] is False  # not approved
    workflow.approve_and_execute(aid, None)
    out = workflow.execute_action(aid, execution.GMAIL_DRAFT)
    assert out["ok"] is False and "Adapter not connected" in out["message"]
    assert out["action"]["execution_status"] == "NOT_STARTED"
    assert "execution_unavailable" in {e["event_type"] for e in state_store.load_events(limit=50)}


def test_out_of_scope_question_is_not_found_with_no_record(retriever):
    stages = []
    analysis = workflow.run_analysis("What is the DR budget for v13?", retriever, progress=stages.append)
    assert analysis["decision_record"] is None
    assert analysis["hits"] == []
    assert analysis["decision"]["answer_found"] is False
    assert analysis["decision"]["blockers"] == []
    assert analysis["decision"]["finding"].startswith("Not found in workspace sources")
    assert stages == ["retrieving"]
    assert state_store.load_decisions() == []


def test_brief_reports_its_stages_in_order(retriever):
    stages = []
    workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever, progress=stages.append)
    assert stages == list(workflow.BRIEF_STAGES) == ["retrieving", "reasoning", "structuring"]


def test_bigquery_counts_only_confirmed_writes_for_this_session(set_config, monkeypatch):
    set_config(demo_mode=False, bigquery_enabled=True, project_id="p")
    inserted = []

    class _Client:
        def insert_rows_json(self, table, rows):
            inserted.extend(rows)
            return [] if rows[0]["event_type"] != "bad" else [{"errors": "boom"}]

    monkeypatch.setattr(analytics, "ensure_table", lambda: object())
    monkeypatch.setattr(analytics, "_client", lambda: _Client())
    analytics.set_session("SES-bq-test")
    assert analytics.log_metric("search_performed", {"q": 1}) is True
    assert analytics.log_metric("bad", {}) is True
    analytics._WRITER.submit(lambda: None).result(timeout=5)  # drain the writer
    assert analytics.session_event_count("SES-bq-test") == 1
    assert analytics.session_event_count("SES-other") == 0
    import json

    assert json.loads(inserted[0]["metadata"])["session_id"] == "SES-bq-test"
    analytics.set_session("")


def test_full_demo_flow_know_decide_approve_act_measure(retriever, sample_docs, live_adapter):
    workflow.register_sample_sources(sample_docs)
    analysis = workflow.run_analysis(workflow.DEFAULT_QUESTION, retriever)
    record = analysis["decision_record"]
    assert record.decision_id.startswith("DEC-")
    assert state_store.get_decision(record.decision_id) is not None
    assert analysis["retrieval_mode"].endswith("(FALLBACK)")

    board, mode = workflow.generate_contracts(retriever, record, analysis["decision"])
    assert board and all(a["decision_id"] == record.decision_id for a in board)
    assert state_store.get_decision(record.decision_id).actions

    aid = board[0]["action_id"]
    out = workflow.approve_and_execute(aid, None, approved_by="Judge")
    assert out["action"]["status"] == "APPROVED" and out["action"]["execution_status"] == "NOT_STARTED"
    out = workflow.execute_action(aid, execution.GMAIL_DRAFT)
    assert out["ok"] and out["execution"].status == "SUCCESS" and out["execution"].mode == "REAL"
    assert out["action"]["execution_status"] == "EXECUTED"
    assert out["action"]["status"] == "IN_PROGRESS" and out["action"]["approved_by"] == "Judge"
    assert "gmail:draft/r-123" in out["message"]

    ok, status = workflow.verify_outcome(aid, "p95 latency = 1.32s", "ACHIEVED")
    assert ok and status == "VERIFIED"

    stored = state_store.get_decision(record.decision_id)
    assert state_store.get_action(aid)["approved_by"] == "Judge"
    assert state_store.executions_for_action(aid)[0].execution_type == "gmail_draft"
    assert "ACHIEVED" in stored.actual_outcome

    events = {e["event_type"] for e in state_store.load_events(limit=200)}
    assert {"knowledge_analyzed", "decision_supported", "action_contract_created", "action_approved", "gmail_draft_created", "outcome_verified"} <= events
