"""The JSON API drives the workflow end to end and keeps the human gate."""
import json

import pytest
from fastapi.testclient import TestClient

from workpilot import api, state_store


@pytest.fixture
def client():
    return TestClient(api.app)


def _analyze(client, sid):
    with client.stream("POST", f"/api/sessions/{sid}/analyze") as res:
        assert res.status_code == 200
        events = [json.loads(line) for line in res.iter_lines() if line]
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "done", events[-1]
    return events


def _through_contracts(client):
    sid = client.post("/api/sessions", json={"scenario_id": "go-live"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/retrieve", json={})
    _analyze(client, sid)
    view = client.post(f"/api/sessions/{sid}/contracts").json()
    return sid, view


def test_config_lists_real_sources_and_scenarios(client):
    cfg = client.get("/api/config").json()
    assert [s["id"] for s in cfg["scenarios"]] == ["go-live", "sec-217", "uat-perf"]
    names = {s["name"] for s in cfg["sources"]}
    assert "issue_tracker.md" in names
    for card in cfg["sources"]:
        assert card["type"] == "Markdown"  # the real file type, never a mocked "PDF · 24 pages"
        assert card["words"] > 0 and card["bytes"] > 0


def test_full_narrative_through_the_api(client, live_adapter):
    sid = client.post("/api/sessions", json={"scenario_id": "go-live"}).json()["session_id"]
    view = client.post(f"/api/sessions/{sid}/retrieve", json={}).json()
    assert view["stage"] == "ANALYZE" and view["retrieved"]["evidence"]
    assert any(s["cited"] for s in view["sources"])

    events = _analyze(client, sid)
    stages = [e["stage"] for e in events if e["type"] == "stage"]
    assert stages and all(s in api.STAGE_LABELS for s in stages)
    view = events[-1]["session"]
    assert view["analysis"]["grounded"] and view["analysis"]["decision_id"].startswith("DEC-")
    assert view["analysis"]["initial_verdict"]["status"] == "NOT READY"
    assert view["stage"] == "ACTIONS"

    view = client.post(f"/api/sessions/{sid}/contracts").json()
    contracts = view["contracts"]
    assert contracts and all(c["approval"] == "Pending" for c in contracts)
    assert view["agent"]["mode"] == "paused" and view["stage"] == "APPROVAL"
    assert client.post(f"/api/sessions/{sid}/resume").status_code == 409  # paused at the human gate

    for c in contracts:
        view = client.post(f"/api/sessions/{sid}/actions/{c['action_id']}/approve").json()
    assert view["progress"]["approved"] == len(contracts) and view["progress"]["executed"] == 0
    assert client.get(f"/api/sessions/{sid}/impact").json()["impact"] is not None  # computed, not a constant

    for c in contracts:
        view = client.post(f"/api/sessions/{sid}/actions/{c['action_id']}/execute", json={"type": "gmail_draft"}).json()
    assert all(c["execution"] == "Executed" and c["receipt"]["reference"] for c in view["contracts"])

    for c in contracts:
        view = client.post(
            f"/api/sessions/{sid}/actions/{c['action_id']}/outcome/confirm",
            json={"actual_outcome": "Verified in staging by the owner", "outcome_status": "ACHIEVED"},
        ).json()
    # Conditions close only through confirmed outcomes: READY FOR APPROVAL needs one per condition.
    conditions = len(view["analysis"]["sections"]["approval_conditions"])
    expected = "READY FOR APPROVAL" if len(contracts) >= conditions else "AT RISK"
    assert view["readiness"]["status"] == expected
    assert view["stage"] == "IMPACT"
    impact = client.get(f"/api/sessions/{sid}/impact").json()["impact"]
    assert impact["hours"] is not None and impact["tiles"]

    view = client.post(f"/api/sessions/{sid}/resume").json()
    assert view["agent_message"] is not None
    if view["agent_message"]["ok"]:
        assert "nothing is waiting on the agent" in view["agent"]["text"]

    # A fresh load (another Cloud Run instance) rebuilds the same view from Firestore.
    again = client.get(f"/api/sessions/{sid}").json()
    assert again["stage"] == "IMPACT" and again["readiness"]["status"] == expected


def test_outcome_cannot_be_confirmed_before_execution(client):
    sid, view = _through_contracts(client)
    aid = view["contracts"][0]["action_id"]
    client.post(f"/api/sessions/{sid}/actions/{aid}/approve")
    res = client.post(
        f"/api/sessions/{sid}/actions/{aid}/outcome/confirm",
        json={"actual_outcome": "done", "outcome_status": "ACHIEVED"},
    )
    assert res.status_code == 409


def test_execute_without_a_connected_adapter_stays_not_started(client):
    sid, view = _through_contracts(client)
    aid = view["contracts"][0]["action_id"]
    client.post(f"/api/sessions/{sid}/actions/{aid}/approve")
    res = client.post(f"/api/sessions/{sid}/actions/{aid}/execute", json={"type": "gmail_draft"})
    assert res.status_code == 409
    assert state_store.get_action(aid)["execution_status"] != "EXECUTED"


def test_actions_from_another_session_are_refused(client):
    sid_a, view_a = _through_contracts(client)
    sid_b = client.post("/api/sessions", json={}).json()["session_id"]
    aid = view_a["contracts"][0]["action_id"]
    assert client.post(f"/api/sessions/{sid_b}/actions/{aid}/approve").status_code == 404
    assert state_store.get_action(aid)["status"] == "PROPOSED"


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/SES-missing").status_code == 404


def test_reject_and_edit(client):
    sid, view = _through_contracts(client)
    first, second = view["contracts"][0]["action_id"], view["contracts"][1]["action_id"]
    view = client.post(f"/api/sessions/{sid}/actions/{first}/fields", json={"owner": "Priya", "due_date": "2026-10-14"}).json()
    edited = next(c for c in view["contracts"] if c["action_id"] == first)
    assert edited["owner"] == "Priya" and edited["due_date"] == "2026-10-14"
    view = client.post(f"/api/sessions/{sid}/actions/{second}/reject").json()
    assert next(c for c in view["contracts"] if c["action_id"] == second)["approval"] == "Rejected"
    assert client.post(f"/api/sessions/{sid}/actions/{second}/fields", json={"owner": "X"}).status_code == 409


def test_my_sessions_lists_only_requested_ids(client):
    a = client.post("/api/sessions", json={}).json()["session_id"]
    client.post("/api/sessions", json={})
    rows = client.get(f"/api/sessions?ids={a}").json()
    assert [r["session_id"] for r in rows] == [a]


def test_status_reports_agent_engine_not_used(client):
    rows = client.get("/api/status").json()["rows"]
    engine = next(r for r in rows if "Agent Engine" in r["technology"])
    assert engine["status"] == "NOT USED"


def test_decision_memory_finds_requested_decisions_by_id(client):
    sid, view = _through_contracts(client)
    did = view["analysis"]["decision_id"]
    rows = client.get(f"/api/memory?ids={did}").json()
    assert [r["decision_id"] for r in rows] == [did]
    assert len(rows[0]["actions"]) == len(view["contracts"])
    assert client.get("/api/memory?ids=DEC-missing").json() == []


UPLOAD = b"""# Change Advisory Board minutes

CAB-77 approved the v12.4 go-live window on 18 Oct 2026, subject to the
rollback rehearsal RB-9 passing. Owner: Lena Ortiz, due 15 Oct 2026.
"""


def test_upload_is_parsed_saved_with_the_session_and_searched(client):
    sid = client.post("/api/sessions", json={"scenario_id": "go-live"}).json()["session_id"]
    res = client.post(f"/api/sessions/{sid}/documents?name=cab_minutes.md", content=UPLOAD,
                      headers={"Content-Type": "application/octet-stream"})
    assert res.status_code == 200, res.text
    card = next(c for c in res.json()["sources"] if c["uploaded"])
    assert card["name"] == "cab_minutes.md" and card["type"] == "Markdown" and card["bytes"] == len(UPLOAD)
    assert card["words"] > 0 and card["parser"]

    # A fresh load (another instance) still has it, and the search finds it.
    view = client.post(f"/api/sessions/{sid}/retrieve", json={"question": "Who owns the rollback rehearsal RB-9 for CAB-77?"}).json()
    assert view["retrieved"]["upload_passages"] >= 1
    assert any(e["source"] == "cab_minutes.md" for e in view["retrieved"]["evidence"])

    # Another session does not see it.
    other = client.post("/api/sessions", json={}).json()["session_id"]
    assert not any(c["uploaded"] for c in client.get(f"/api/sessions/{other}").json()["sources"])

    view = client.delete(f"/api/sessions/{sid}/documents/{card['source_id']}").json()
    assert not any(c["uploaded"] for c in view["sources"])


def test_upload_rules(client):
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    post = lambda name, body=UPLOAD: client.post(f"/api/sessions/{sid}/documents?name={name}", content=body)
    assert post("payload.exe").status_code == 400
    assert post("issue_tracker.md").status_code == 409  # a workspace document's name
    assert post("empty.md", b"").status_code == 400
    client.post(f"/api/sessions/{sid}/retrieve", json={})
    _analyze(client, sid)
    assert post("late.md").status_code == 409  # evidence is fixed once a decision exists


def test_run_all_approved_executes_each_with_a_real_receipt(client, live_adapter):
    sid, view = _through_contracts(client)
    assert client.post(f"/api/sessions/{sid}/execute-approved", json={"type": "gmail_draft"}).status_code == 409
    for c in view["contracts"]:
        client.post(f"/api/sessions/{sid}/actions/{c['action_id']}/approve")
    view = client.post(f"/api/sessions/{sid}/execute-approved", json={"type": "gmail_draft"}).json()
    assert all(r["ok"] for r in view["results"]) and len(view["results"]) == len(view["contracts"])
    assert all(c["execution"] == "Executed" and c["receipt"]["reference"].startswith("gmail:draft/") for c in view["contracts"])
    assert len(live_adapter.calls) == len(view["contracts"])

    events = client.get(f"/api/sessions/{sid}/audit").json()["events"]
    kinds = {e["event_type"] for e in events}
    assert {"action_approved", "gmail_draft_created"} <= kinds and all(e["event_id"] for e in events)


def test_run_all_without_a_connected_adapter_executes_nothing(client):
    sid, view = _through_contracts(client)
    for c in view["contracts"]:
        client.post(f"/api/sessions/{sid}/actions/{c['action_id']}/approve")
    assert client.post(f"/api/sessions/{sid}/execute-approved", json={"type": "gmail_draft"}).status_code == 502
    assert all(c["execution"] == "Not started" for c in client.get(f"/api/sessions/{sid}").json()["contracts"])


def test_preparing_contracts_twice_does_not_duplicate_them(client):
    sid, view = _through_contracts(client)
    again = client.post(f"/api/sessions/{sid}/contracts").json()
    assert [c["action_id"] for c in again["contracts"]] == [c["action_id"] for c in view["contracts"]]


def test_resume_before_execution_completes_and_stays_honest(client):
    sid, view = _through_contracts(client)
    for c in view["contracts"]:
        client.post(f"/api/sessions/{sid}/actions/{c['action_id']}/approve")
    msg = client.post(f"/api/sessions/{sid}/resume").json()["agent_message"]
    assert msg["ok"] is True and msg["readiness"] == "NOT READY"
    assert client.get(f"/api/sessions?ids={sid}").json()[0]["readiness"] == "NOT READY"
