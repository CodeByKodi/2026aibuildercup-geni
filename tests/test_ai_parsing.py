import pytest

from workpilot import ai
from workpilot.ai import AIServiceError, parse_actions, parse_json
from workpilot.models import SourceChunk


def test_parse_json_handles_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go:\n[{"title": "x"}]\nThanks!') == [{"title": "x"}]
    assert parse_json('{"nested": {"b": [1, 2]}}') == {"nested": {"b": [1, 2]}}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_parse_actions_tolerates_lists_and_wrappers():
    data = {"actions": [
        {"title": "Fix", "owner": "Mei", "due_date": "12 Oct", "priority": "High", "rationale": "r", "source": ["a.md", "b.md"], "expected_outcome": "done", "extra": 1},
        "not a dict",
    ]}
    actions = parse_actions(data, decision_id="DEC-1")
    assert len(actions) == 1
    assert actions[0].source == "a.md, b.md"
    assert actions[0].decision_id == "DEC-1"


def test_normalize_decision_handles_shape_drift():
    out = ai.normalize_decision({"answer": "finding", "blockers": [{"title": "b", "evidence": "x.md", "impact": "i", "action": "a", "priority": "high"}, "bad"]})
    assert out["finding"] == "finding"
    assert out["blockers"][0]["evidence"] == ["x.md"]
    assert out["blockers"][0]["priority"] == "High"
    assert out["blockers"][0]["recommendation"] == "a"
    assert ai.normalize_decision("plain text")["finding"] == "plain text"


def test_demo_analyze_decision_is_grounded_in_chunks(sample_docs):
    chunks = [SourceChunk("uat_meeting_notes.md", sample_docs["uat_meeting_notes.md"])]
    decision, mode = ai.analyze_decision("Are we ready to go live?", chunks)
    assert mode == ai.MODE_DEMO
    owners = {b["owner"] for b in decision["blockers"]}
    assert {"Priya", "Arjun", "Mei"} <= owners
    md = ai.decision_to_markdown(decision)
    assert "Blocker 1" in md and "Recommendation this week" in md


def test_generate_raises_clear_error_in_demo_mode():
    with pytest.raises(AIServiceError, match="DEMO_MODE"):
        ai.generate("hi")
    assert ai.status()[0] == "FALLBACK"


def test_extract_actions_reuses_adk_proposed_actions(set_config, monkeypatch):
    set_config(demo_mode=False)
    monkeypatch.setattr(ai, "generate", lambda *a, **k: pytest.fail("should not call Gemini when ADK proposed actions exist"))
    decision = {"proposed_actions": [{"title": "Submit evidence", "owner": "Priya", "due_date": "7 Oct", "priority": "Critical", "rationale": "r", "source": "meeting_notes.md", "expected_outcome": "cert"}]}
    actions, mode = ai.extract_actions([], decision_id="DEC-9", decision=decision)
    assert mode == ai.MODE_ADK
    assert actions[0].title == "Submit evidence" and actions[0].decision_id == "DEC-9"


def test_extract_actions_direct_gemini_parses_fenced_json(set_config, monkeypatch):
    set_config(demo_mode=False)
    monkeypatch.setattr(ai, "generate", lambda *a, **k: '```json\n[{"title": "A", "owner": "B", "due_date": "TBD", "priority": "Low", "rationale": "", "source": "x.md", "expected_outcome": ""}]\n```')
    actions, mode = ai.extract_actions([SourceChunk("x.md", "text")])
    assert mode == ai.MODE_GEMINI and actions[0].title == "A"


def test_extract_actions_falls_back_when_gemini_is_unparseable(set_config, monkeypatch):
    set_config(demo_mode=False)
    monkeypatch.setattr(ai, "generate", lambda *a, **k: "I cannot help with that.")
    actions, mode = ai.extract_actions([SourceChunk("security_assessment.md", "Mei SEC-217 token")])
    assert mode == ai.MODE_CACHED
    assert actions


def test_analyze_decision_falls_back_when_vertex_fails(set_config, monkeypatch):
    set_config(demo_mode=False)
    monkeypatch.setattr(ai, "generate", lambda *a, **k: (_ for _ in ()).throw(AIServiceError("timeout")))
    decision, mode = ai.analyze_decision("Are we ready to go live?", [SourceChunk("security_assessment.md", "Mei SEC-217")])
    assert mode == ai.MODE_CACHED
    assert "timeout" in decision["fallback_reason"]
    assert decision["blockers"]
    assert decision["open_questions"] == []


def test_no_evidence_returns_not_found_without_blockers(set_config, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "generate", lambda *a, **k: calls.append(1) or "{}")
    decision, mode = ai.analyze_decision("What is the DR budget for v13?", [])
    assert mode == ai.MODE_NO_EVIDENCE
    assert decision["answer_found"] is False
    assert decision["finding"] == "Not found in workspace sources"
    assert decision["blockers"] == [] and decision["approval_conditions"] == []
    set_config(demo_mode=False)
    decision, _ = ai.analyze_decision("What is the DR budget for v13?", [])
    assert calls == []  # Gemini is never asked to answer without evidence


def test_model_saying_answer_not_found_drops_blockers(set_config, monkeypatch):
    set_config(demo_mode=False)
    monkeypatch.setattr(
        ai,
        "generate",
        lambda *a, **k: '{"answer_found": false, "finding": "The sources do not cover DR budgets.", "blockers": [{"blocker": "x"}]}',
    )
    decision, mode = ai.analyze_decision("What is the DR budget?", [SourceChunk("release_status_report.md", "v12.4")])
    assert mode == ai.MODE_GEMINI
    assert decision["answer_found"] is False and decision["blockers"] == []


def test_assess_outcome_demo():
    suggestion, _ = ai.assess_outcome("p95 < 1.5s", "p95 latency met at 1.32s")
    assert suggestion["suggested_status"] == "ACHIEVED"


def test_conditions_and_questions_sent_as_objects_become_sentences():
    decision = ai.normalize_decision({
        "finding": "Not ready",
        "approval_conditions": [
            {"condition": "SEC-217 verified closed", "evidence": ["security_assessment.md"]},
            "p95 below 1.5 seconds",
            {"evidence": ["x.md"]},
        ],
        "open_questions": [{"question": "Who signs off UAT?"}, "Is the CAB booked?"],
    })
    assert decision["approval_conditions"] == ["SEC-217 verified closed", "p95 below 1.5 seconds"]
    assert decision["open_questions"] == ["Who signs off UAT?", "Is the CAB booked?"]
