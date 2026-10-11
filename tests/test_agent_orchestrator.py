"""The ADK agent's turns: scoped tools, real traces, persistence, and a deterministic fallback.

ADK itself is replaced by a scripted "model" that calls the tools it is offered,
so these tests cover WorkPilot's orchestration, not Gemini.
"""
import json

import pytest

from workpilot import adk_orchestrator, ai, execution, state_store, workflow, workflow_session
from workpilot.agent import orchestrator
from workpilot.models import ActionStatus
from workpilot.retrieval import EnterpriseRetriever

from test_agent_tools import ACTIONS, BLOCKERS, CONDITIONS


@pytest.fixture
def retriever(sample_docs):
    r = EnterpriseRetriever()
    r.build(sample_docs)
    return r


@pytest.fixture
def agent(monkeypatch):
    """Install a scripted agent turn; records which tools each turn was offered."""
    offered = []

    def install(script):
        def run_turn(instruction, message, tools, final_tools=()):
            by_name = {t.__name__: t for t in tools}
            offered.append(sorted(by_name))
            assert all(name in by_name for name in final_tools)
            return script(by_name, message)

        monkeypatch.setattr(adk_orchestrator, "configured", lambda: True)
        monkeypatch.setattr(adk_orchestrator, "run_turn", run_turn)
        return offered

    return install


def decide_script(tools, message):
    tools["get_workflow_context"]()
    tools["search_knowledge"](message)
    tools["create_decision"](
        "Not ready: three conditions open.", "Validate SEC-217, then retest p95.", "Evidence shows open gates.",
        "All conditions verified", "NOT READY", json.dumps(BLOCKERS), json.dumps(CONDITIONS), "[]", True,
    )
    return "Customer Portal v12.4 is not ready. Three conditions remain open."


def contracts_script(tools, message):
    decision_id = message.split("\n")[0].split(": ")[1]
    tools["get_workflow_context"]()
    tools["search_knowledge"]("owners and deadlines for SEC-217 and PERF-103")
    tools["create_action_contracts"](decision_id, json.dumps(ACTIONS))
    return "A person must approve each contract."


def test_generate_brief_stores_the_agent_steps_that_actually_ran(agent, retriever, sample_docs):
    agent(decide_script)
    updates = workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-2", documents=sample_docs)
    assert updates["session_decision_id"]
    assert [t.split(" · ")[0] for t in updates["agent_trace"]] == ["get_workflow_context", "search_knowledge", "create_decision"]


def test_turn_that_never_records_a_decision_falls_back(agent, retriever, sample_docs):
    agent(lambda tools, message: tools["search_knowledge"](message) and "I think it is fine.")
    assert orchestrator.run_decide(workflow.DEFAULT_QUESTION, retriever, sample_docs, "SES-4") is None


def test_out_of_scope_question_is_not_found_without_a_second_model_call(agent, monkeypatch, retriever, sample_docs):
    agent(decide_script)
    monkeypatch.setattr(workflow, "run_analysis", lambda *a, **k: pytest.fail("pipeline must not run"))
    updates = workflow_session.generate_brief("What is the DR budget for Portal v13?", retriever, session_id="SES-5", documents=sample_docs)
    analysis = updates["last_analysis"]
    assert analysis["decision_record"] is None and analysis["hits"] == []
    assert analysis["decision"]["answer_found"] is False
    assert updates["session_decision_id"] == ""


def test_agent_saying_not_found_despite_evidence_gets_a_second_opinion(agent, retriever, sample_docs):
    def unsure(tools, message):
        tools["search_knowledge"](message)
        tools["create_decision"]("", "", "", "", "", "[]", "[]", "[]", False)
        return "The evidence does not answer this."

    agent(unsure)
    updates = workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-6", documents=sample_docs)
    assert updates["session_decision_id"]  # the pipeline found the answer the agent missed
    assert updates["last_analysis"]["decision"]["answer_found"] is True


def test_contract_turn_proposes_scoped_contracts_that_wait_for_a_person(agent, retriever, sample_docs):
    offered = agent(decide_script)
    brief = workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-7", documents=sample_docs)
    agent(contracts_script)
    analysis = brief["last_analysis"]
    updates = workflow_session.prepare_contracts(
        retriever, analysis["decision_record"], analysis["decision"], session_id="SES-7", documents=sample_docs, trace=brief["agent_trace"]
    )
    assert offered[-1] == sorted(orchestrator.CONTRACT_TOOLS)
    assert updates["contracts_mode"] == ai.MODE_ADK
    scoped = [a for a in updates["actions"] if a["action_id"] in updates["session_action_ids"]]
    assert len(scoped) == 2  # go-live approval stays with a person
    assert all(a["status"] == ActionStatus.PROPOSED for a in scoped)
    assert all(a["decision_id"] == analysis["decision_record"].decision_id for a in scoped)
    tools = [t.split(" · ")[0] for t in updates["agent_trace"]]
    assert tools[-1] == "create_action_contracts" and tools[:3] == ["get_workflow_context", "search_knowledge", "create_decision"]


def test_contract_turn_that_proposes_nothing_falls_back(agent, retriever, sample_docs):
    agent(decide_script)
    brief = workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-8", documents=sample_docs)
    agent(lambda tools, message: "Done.")
    analysis = brief["last_analysis"]
    updates = workflow_session.prepare_contracts(retriever, analysis["decision_record"], analysis["decision"], session_id="SES-8")
    assert updates["contracts_mode"] != ai.MODE_ADK
    assert updates["session_action_ids"]


def executed_action(agent, retriever, sample_docs, session_id):
    agent(decide_script)
    brief = workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id=session_id, documents=sample_docs)
    agent(contracts_script)
    analysis = brief["last_analysis"]
    updates = workflow_session.prepare_contracts(retriever, analysis["decision_record"], analysis["decision"], session_id=session_id)
    aid = updates["session_action_ids"][0]
    workflow.approve_and_execute(aid, None, approved_by="Maya")  # a person's clicks
    workflow.execute_action(aid, execution.GMAIL_DRAFT)
    return state_store.get_action(aid)


def test_reassess_turn_proposes_an_outcome_and_records_nothing(agent, retriever, sample_docs, live_adapter):
    action = executed_action(agent, retriever, sample_docs, "SES-9")

    def reassess(tools, message):
        fields = dict(line.split(": ", 1) for line in message.split("\n"))
        tools["evaluate_outcome"](fields["action_id"], fields["Actual outcome evidence"])
        tools["assess_readiness"](fields["decision_id"])
        return "Proposed ACHIEVED. Two approval conditions remain open; a person confirms the outcome."

    offered = agent(reassess)
    result = workflow_session.suggest_outcome(action, "SEC-217 verified closed", retriever, "SES-9", sample_docs)
    assert offered[-1] == sorted(orchestrator.REASSESS_TOOLS)  # no tool can confirm the outcome
    assert result["mode"] == ai.MODE_ADK and result["suggested_status"]
    assert result["summary"].startswith("Proposed")
    assert [t.split(" · ")[0] for t in result["agent_trace"]] == ["evaluate_outcome", "assess_readiness"]
    assert state_store.get_action(action["action_id"])["outcome_status"] == "PENDING"


def test_reassess_falls_back_to_a_single_assessment(agent, retriever, sample_docs, live_adapter):
    action = executed_action(agent, retriever, sample_docs, "SES-10")
    agent(lambda tools, message: "I cannot tell.")
    result = workflow_session.suggest_outcome(action, "SEC-217 verified closed", retriever, "SES-10", sample_docs)
    assert result["mode"] != ai.MODE_ADK and result["suggested_status"] and result["agent_trace"] == []


def test_analytics_record_the_agent_turn_duration_not_zero(agent, monkeypatch, retriever, sample_docs):
    durations = {}
    monkeypatch.setattr(workflow.analytics, "log_metric", lambda event, payload, **kw: durations.setdefault(event, kw.get("duration_ms")))

    def slow_decide(tools, message):
        import time

        time.sleep(0.05)  # the model's reasoning time
        return decide_script(tools, message)

    agent(slow_decide)
    workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-11", documents=sample_docs)
    assert durations["knowledge_analyzed"] >= 50


def resume_script(tools, message):
    decision_id = message.split("\n")[0].split(": ")[1]
    tools["get_workflow_context"]()
    tools["get_action_status"](decision_id)
    tools["assess_readiness"](decision_id)
    tools["calculate_impact"](decision_id)
    return "The release is ready for approval."  # the model's wording; the UI shows the tool's readiness


PAY_482 = {"title": "Retest PAY-482 and complete UAT sign-off", "owner": "Priya", "due_date": "11 Oct 2026",
           "priority": "Medium", "rationale": "UAT gate", "source": "uat_meeting_notes.md",
           "expected_outcome": "PAY-482 retest passed and UAT signed off"}


def three_contracts_script(tools, message):
    decision_id = message.split("\n")[0].split(": ")[1]
    tools["get_workflow_context"]()
    tools["search_knowledge"]("owners and deadlines for SEC-217, PERF-103 and PAY-482")
    tools["create_action_contracts"](decision_id, json.dumps(ACTIONS[:2] + [PAY_482]))
    return ""


def test_agent_pauses_at_the_human_gate_and_resumes_from_firestore(agent, retriever, sample_docs, live_adapter):
    agent(decide_script)
    state = {"ui_session_id": "SES-12", "documents": sample_docs}
    state.update(workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-12", documents=sample_docs))
    agent(three_contracts_script)
    analysis = state["last_analysis"]
    state.update(workflow_session.prepare_contracts(retriever, analysis["decision_record"], analysis["decision"], session_id="SES-12"))

    status = workflow_session.agent_status(state)
    assert status["mode"] == "paused" and status["stage"] == "APPROVAL" and status["can_resume"] is False
    assert "waiting for a person to approve 3 of 3" in status["text"]

    for aid in state["session_action_ids"]:  # people approve and execute
        workflow.approve_and_execute(aid, None, approved_by="Maya")
        workflow.execute_action(aid, execution.GMAIL_DRAFT)
    state["actions"] = state_store.load_actions()
    status = workflow_session.agent_status(state)
    assert status["can_resume"] and status["mode"] == "paused" and "confirm on Outcome & Impact" in status["text"]

    offered = agent(resume_script)
    state.update(workflow_session.resume_agent(state, retriever))
    message = state["agent_message"]
    assert offered[-1] == sorted(orchestrator.RESUME_TOOLS)  # read-only: no tool approves or confirms
    assert message["ok"] and message["readiness"] != "READY FOR APPROVAL"  # computed, not the model's claim
    assert [s.split(" · ")[0] for s in message["steps"]][:1] == ["get_workflow_context"]
    assert workflow_session.agent_message_current(state)

    for aid in state["session_action_ids"]:  # people confirm outcomes
        workflow.verify_outcome(aid, "Verified by the owner", "ACHIEVED", verified_by="Maya")
    state["actions"] = state_store.load_actions()
    assert not workflow_session.agent_message_current(state)  # people acted since: out of date
    assert workflow_session.agent_status(state)["mode"] == "ready"
    state.update(workflow_session.resume_agent(state, retriever))
    assert state["agent_message"]["readiness"] == "READY FOR APPROVAL"
    assert state["agent_message"]["impact_hours"] > 0

    saved = workflow_session.session_payload(state)
    assert saved["agent_message"]["readiness"] == "READY FOR APPROVAL"
    assert workflow_session.restore(saved, state["actions"])["agent_message"] == saved["agent_message"]


def test_failed_agent_turn_still_resumes_from_the_saved_workflow(agent, retriever, sample_docs, live_adapter):
    """If Gemini does not complete the resume turn, readiness is recomputed from Firestore and labelled so."""
    agent(decide_script)
    state = {"ui_session_id": "SES-13", "documents": sample_docs}
    state.update(workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-13", documents=sample_docs))
    agent(contracts_script)
    analysis = state["last_analysis"]
    state.update(workflow_session.prepare_contracts(retriever, analysis["decision_record"], analysis["decision"], session_id="SES-13"))
    for aid in state["session_action_ids"]:
        workflow.approve_and_execute(aid, None, approved_by="Maya")
    state["actions"] = state_store.load_actions()
    agent(lambda tools, message: (_ for _ in ()).throw(RuntimeError("quota")))
    message = workflow_session.resume_agent(state, retriever)["agent_message"]
    assert message["ok"] is True and message["fallback"] is True
    assert message["text"].startswith("Gemini did not complete the agent turn")
    assert message["readiness"] == "NOT READY"  # nothing executed or confirmed yet
    assert "0 executed" in message["text"]
    assert message["board"] == workflow_session.board_fingerprint(state)


def test_a_condition_without_a_contract_keeps_the_release_at_risk(agent, retriever, sample_docs, live_adapter):
    """Three approval conditions, two contracts: confirming both cannot make the release ready."""
    agent(decide_script)
    state = {"ui_session_id": "SES-14", "documents": sample_docs}
    state.update(workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-14", documents=sample_docs))
    agent(contracts_script)
    analysis = state["last_analysis"]
    state.update(workflow_session.prepare_contracts(retriever, analysis["decision_record"], analysis["decision"], session_id="SES-14"))
    for aid in state["session_action_ids"]:
        workflow.approve_and_execute(aid, None, approved_by="Maya")
        workflow.execute_action(aid, execution.GMAIL_DRAFT)
        workflow.verify_outcome(aid, "Verified by the owner", "ACHIEVED", verified_by="Maya")
    state["actions"] = state_store.load_actions()
    agent(resume_script)
    state.update(workflow_session.resume_agent(state, retriever))
    assert state["agent_message"]["readiness"] == "AT RISK"


def test_rate_limit_is_retried_once_only_before_any_tool_ran(agent, monkeypatch, retriever, sample_docs):
    monkeypatch.setattr(orchestrator, "RETRY_SECONDS", 0)
    calls = {"n": 0}

    def flaky(tools, message):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return decide_script(tools, message)

    agent(flaky)
    analysis = orchestrator.run_decide(workflow.DEFAULT_QUESTION, retriever, sample_docs, "SES-15")
    assert calls["n"] == 2 and analysis["decision_record"] is not None

    calls["n"] = 0

    def fails_after_recording(tools, message):
        calls["n"] += 1
        decide_script(tools, message)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    agent(fails_after_recording)
    before = len(state_store.load_events(limit=500))
    assert orchestrator.run_decide(workflow.DEFAULT_QUESTION, retriever, sample_docs, "SES-16") is None
    assert calls["n"] == 1  # a decision was already recorded, so no retry and no duplicate
    assert len(state_store.load_events(limit=500)) > before


def test_decide_turn_uses_only_its_tools_and_records_a_grounded_decision(agent, retriever, sample_docs):
    offered = agent(decide_script)
    stages = []
    analysis = orchestrator.run_decide(workflow.DEFAULT_QUESTION, retriever, sample_docs, "SES-1", stages.append)

    assert offered == [sorted(orchestrator.DECIDE_TOOLS)]  # no contracts, no outcomes in this turn
    assert analysis["ai_mode"] == ai.MODE_ADK
    record = analysis["decision_record"]
    assert record is not None and record.session_id == "SES-1"
    assert state_store.get_decision(record.decision_id) is not None
    assert [s["tool"] for s in analysis["agent_steps"]] == ["get_workflow_context", "search_knowledge", "create_decision"]
    assert stages == ["retrieving", "reasoning", "structuring"]
    assert analysis["hits"]
    assert adk_orchestrator.runtime_status()["status"] == "ACTIVE"


def test_failed_turn_falls_back_to_the_labelled_pipeline(agent, monkeypatch, retriever, sample_docs):
    def broken(tools, message):
        raise RuntimeError("Vertex quota exceeded")

    agent(broken)
    updates = workflow_session.generate_brief(workflow.DEFAULT_QUESTION, retriever, session_id="SES-3", documents=sample_docs)
    analysis = updates["last_analysis"]
    assert analysis["ai_mode"] != ai.MODE_ADK and analysis["decision_record"] is not None
    assert adk_orchestrator.runtime_status()["status"] == "FALLBACK"
    assert "quota" in adk_orchestrator.runtime_status()["detail"]
