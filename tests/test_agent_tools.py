"""The WorkPilot agent's tools: grounded, persistent, traced, and unable to cross the human gate."""
import inspect
import json

import pytest

from workpilot import execution, state_store, workflow, workflow_session
from workpilot.agent import TOOL_GROUPS, AgentRun, build_tools
from workpilot.retrieval import EnterpriseRetriever

FORBIDDEN = ("approve", "reject", "execute", "confirm", "record_outcome", "verify", "send")

BLOCKERS = [
    {"blocker": "SEC-217 token lifetime", "evidence": ["security_assessment.md", "made_up.md"],
     "business_impact": "Security gate fails", "recommendation": "Validate SEC-217", "owner": "Mei",
     "priority": "High", "due_date": "12 Oct 2026"},
    {"blocker": "PERF-103 p95 2.4s", "evidence": ["performance_test_report.md"],
     "business_impact": "Performance gate fails", "recommendation": "Deploy cache and retest", "owner": "Arjun",
     "priority": "High", "due_date": "13 Oct 2026"},
]
CONDITIONS = ["SEC-217 verified closed", "p95 < 1.5 seconds", "PAY-482 retest passed"]
ACTIONS = [
    {"title": "Validate SEC-217 remediation", "owner": "Mei", "due_date": "12 Oct 2026", "priority": "High",
     "rationale": "Security gate", "source": "security_assessment.md", "expected_outcome": "SEC-217 verified closed"},
    {"title": "Deploy cache and rerun performance test", "owner": "Arjun", "due_date": "13 Oct 2026",
     "priority": "High", "rationale": "p95 2.4s", "source": "invented.md", "expected_outcome": "p95 < 1.5 seconds"},
    {"title": "Approve production go-live", "owner": "Maya", "due_date": "16 Oct 2026", "priority": "High",
     "rationale": "gate", "source": "release_status_report.md", "expected_outcome": "released"},
]


@pytest.fixture
def run(sample_docs):
    retriever = EnterpriseRetriever()
    retriever.build(sample_docs)
    return AgentRun(session_id="SES-agent", retriever=retriever, documents=sample_docs)


def tools_by_name(run):
    return {t.__name__: t for t in build_tools(run)}


def decide(tools):
    tools["search_knowledge"](workflow.DEFAULT_QUESTION)
    return tools["create_decision"](
        "Not ready: three conditions open.", "Validate SEC-217, then retest p95.", "Evidence shows open gates.",
        "All conditions verified", "NOT READY", json.dumps(BLOCKERS), json.dumps(CONDITIONS), "[]", True,
    )


def test_tools_match_the_architecture_and_cannot_cross_the_human_gate(run):
    names = list(tools_by_name(run))
    assert names == [
        "search_knowledge", "get_document", "create_decision", "create_action_contracts",
        "get_action_status", "evaluate_outcome", "assess_readiness", "calculate_impact", "get_workflow_context",
    ]
    assert set(TOOL_GROUPS) == {"Knowledge Tool", "Decision Tool", "Action Tool", "Outcome Tool", "Workflow memory"}
    for name in names:
        assert not any(word in name for word in FORBIDDEN), name


def test_every_tool_becomes_a_clean_adk_function_declaration(run):
    from google.adk.tools.function_tool import FunctionTool

    for tool in build_tools(run):
        params = inspect.signature(tool).parameters.values()
        assert all(p.default is inspect.Parameter.empty for p in params), f"{tool.__name__} has a default"
        assert tool.__doc__ and len(tool.__doc__.strip()) > 40
        declaration = FunctionTool(tool)._get_declaration()
        assert declaration.name == tool.__name__
        assert declaration.description


def test_decision_must_be_grounded_in_retrieved_evidence(run):
    tools = tools_by_name(run)
    out = tools["create_decision"]("f", "r", "x", "o", "NOT READY", "[]", "[]", "[]", True)
    assert "search_knowledge first" in out["error"]
    assert state_store.load_decisions() == []

    result = decide(tools)
    assert result["recorded"] and result["verdict"] == "NOT READY — 3 of 3 approval conditions open"
    assert "1 citation" in result["note"]  # made_up.md is not in the evidence
    record = state_store.get_decision(result["decision_id"])
    assert record.session_id == "SES-agent" and record.approval_conditions == CONDITIONS
    assert record.ai_mode.endswith("orchestrated by ADK") and record.evidence


def test_out_of_scope_question_records_nothing(run):
    tools = tools_by_name(run)
    found = tools["search_knowledge"]("What is the DR budget for v13?")
    assert found["found"] is False and "v13" in found["summary"]
    out = tools["create_decision"]("f", "r", "x", "o", "NOT READY", "[]", "[]", "[]", True)
    assert "error" in out  # no evidence, nothing recorded
    assert state_store.load_decisions() == []


def test_action_contracts_start_proposed_and_drop_go_live_approval(run):
    tools = tools_by_name(run)
    decision_id = decide(tools)["decision_id"]
    out = tools["create_action_contracts"](decision_id, json.dumps(ACTIONS))
    titles = {c["title"] for c in out["proposed"]}
    assert titles == {"Validate SEC-217 remediation", "Deploy cache and rerun performance test"}
    assert all(c["approval"] == "Pending" and c["execution"] == "Not started" for c in out["proposed"])
    assert out["awaiting_human_approval"] is True
    perf = next(a for a in state_store.actions_for_decision(decision_id) if a["owner"] == "Arjun")
    assert perf["source"] == "TBD"  # invented.md was not retrieved, so it is not cited

    assert "Unknown decision" in tools["create_action_contracts"]("DEC-nope", "[]")["error"]
    assert "not valid JSON" in tools["create_action_contracts"](decision_id, "{oops")["error"]


def test_contracts_cannot_target_another_sessions_decision(run, sample_docs):
    decision_id = decide(tools_by_name(run))["decision_id"]
    other = AgentRun(session_id="SES-other", retriever=run.retriever, documents=sample_docs)
    out = tools_by_name(other)["create_action_contracts"](decision_id, json.dumps(ACTIONS[:1]))
    assert "different session" in out["error"]


def test_outcome_is_proposed_never_recorded_and_readiness_never_says_approved(run, live_adapter):
    tools = tools_by_name(run)
    decision_id = decide(tools)["decision_id"]
    tools["create_action_contracts"](decision_id, json.dumps(ACTIONS[:1]))
    aid = state_store.actions_for_decision(decision_id)[0]["action_id"]

    assert tools["evaluate_outcome"](aid, "SEC-217 closed")["eligible"] is False  # not executed yet
    workflow.approve_and_execute(aid, None, approved_by="Maya")  # a person's click
    workflow.execute_action(aid, execution.GMAIL_DRAFT)  # a person's click
    proposal = tools["evaluate_outcome"](aid, "SEC-217 verified closed, regression test passed")
    assert proposal["eligible"] and proposal["requires_human_confirmation"] is True
    assert state_store.get_action(aid)["outcome_status"] == "PENDING"  # nothing recorded by the agent

    workflow.verify_outcome(aid, "SEC-217 verified closed", proposal["proposed_status"], verified_by="Maya")
    readiness = tools["assess_readiness"](decision_id)
    assert readiness["status"] in {"AT RISK", "NOT READY", "READY FOR APPROVAL"}
    assert "APPROVED" not in readiness["status"].replace("READY FOR APPROVAL", "")
    assert readiness["total"] == 3  # the decision's approval conditions stay the denominator

    impact = tools["calculate_impact"](decision_id)
    assert impact["metrics"]["knowledge_sources_analyzed"] == 6 and "assumptions" in impact
    events = {e["event_type"] for e in state_store.load_events(limit=200)}
    assert {"outcome_evaluated", "readiness_assessed", "impact_calculated"} <= events


def test_workflow_context_resumes_from_the_saved_session(run):
    tools = tools_by_name(run)
    assert tools["get_workflow_context"]()["stage"] == "KNOWLEDGE"

    state = workflow_session.fresh_state("SES-agent")
    state.update(workflow_session.generate_brief(workflow.DEFAULT_QUESTION, run.retriever))
    record = state["last_analysis"]["decision_record"]
    state.update(workflow_session.prepare_contracts(run.retriever, record, state["last_analysis"]["decision"]))
    workflow_session.save(state)

    context = tools["get_workflow_context"]()
    assert context["stage"] == "APPROVAL"
    assert context["decision_id"] == record.decision_id
    assert context["awaiting_human_approval"]
    assert "cannot approve" in context["guardrails"]
    assert state_store.get_session("SES-agent")["stage"] == "APPROVAL"


def test_every_call_is_traced_and_failures_become_messages(run):
    tools = tools_by_name(run)
    tools["search_knowledge"](workflow.DEFAULT_QUESTION)
    out = tools["create_decision"]("f", "r", "x", "o", "NOT READY", "{not json", "[]", "[]", True)
    assert "not valid JSON" in out["error"]
    assert [t["tool"] for t in run.trace] == ["search_knowledge", "create_decision"]
    assert run.trace[0]["ok"] is True and run.trace[1]["ok"] is False
    assert "via Local TF-IDF" in run.trace[0]["summary"]


def test_get_document_reads_only_workspace_sources(run):
    tools = tools_by_name(run)
    assert "SEC-217" in tools["get_document"]("security_assessment.md")["text"]
    assert "available" in tools["get_document"]("../../etc/passwd")
