"""The workflow session is driven without a UI: persisted, restored, and advanced."""
import pytest

from workpilot import ai, state_store, workflow, workflow_session
from workpilot.retrieval import EnterpriseRetriever


@pytest.fixture
def retriever(sample_docs):
    r = EnterpriseRetriever()
    r.build(sample_docs)
    return r


def test_full_session_runs_without_a_ui(retriever):
    state = workflow_session.fresh_state("SES-test1")
    state.update(workflow_session.retrieve(workflow.DEFAULT_QUESTION, retriever))
    assert state["retrieved"]["hits"] and workflow_session.flags(state)["Know"]
    assert state["agent_trace"][0].startswith("search_knowledge")

    state.update(workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever))
    assert workflow_session.has_grounded_analysis(state)
    assert state["session_decision_id"].startswith("DEC-")
    assert state["agent_trace"][-1] == ai.MODE_DEMO  # what ran, not a fixed script

    record = state["last_analysis"]["decision_record"]
    state.update(workflow_session.prepare_contracts(retriever, record, state["last_analysis"]["decision"]))
    board = workflow_session.session_board(state)
    assert board and all(a["status"] == "PROPOSED" for a in board)
    assert state["contracts_for"] == record.decision_id


def test_save_and_restore_survive_a_restart(retriever):
    state = workflow_session.fresh_state("SES-restore")
    state.update(workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever))
    record = state["last_analysis"]["decision_record"]
    state.update(workflow_session.prepare_contracts(retriever, record, state["last_analysis"]["decision"]))
    state["step"] = "Approve"
    assert workflow_session.save(state) is True

    stored = state_store.get_session("SES-restore")
    restored = workflow_session.fresh_state("SES-other")
    restored.update(workflow_session.restore(stored, current_actions=state_store.load_actions()))
    assert restored["ui_session_id"] == "SES-restore"
    assert restored["step"] == "Approve"
    assert restored["session_decision_id"] == record.decision_id
    assert restored["last_analysis"]["decision_record"].decision_id == record.decision_id
    assert {a["action_id"] for a in workflow_session.session_board(restored)} == {
        a["action_id"] for a in workflow_session.session_board(state)
    }
    assert workflow_session.flags(restored) == workflow_session.flags(state)


def test_restore_recreates_missing_records_from_the_snapshot():
    stored = {
        "session_id": "SES-x",
        "current_step": "Bogus",
        "decision_id": "DEC-gone",
        "analysis": {"decision_id": "DEC-gone", "decision": {"finding": "Not ready"}, "hits": []},
        "action_contracts": [{"action_id": "ACT-1", "title": "Fix", "owner": "Mei", "decision_id": "DEC-gone"}],
    }
    updates = workflow_session.restore(stored)
    assert updates["step"] == "Know"  # unknown step falls back safely
    assert updates["last_analysis"]["decision_record"].finding == "Not ready"
    assert state_store.get_action("ACT-1") is not None  # contract re-persisted
    assert updates["session_action_ids"] == ["ACT-1"]


def test_out_of_scope_brief_is_not_grounded(retriever):
    state = workflow_session.fresh_state()
    state.update(workflow_session.generate_brief("What is the DR budget for v13?", retriever))
    assert workflow_session.has_grounded_analysis(state) is False
    assert state["session_decision_id"] == ""
    assert workflow_session.flags(state)["Decide"] is False


def test_save_without_a_session_id_is_a_no_op():
    assert workflow_session.save({"ui_session_id": ""}) is False
