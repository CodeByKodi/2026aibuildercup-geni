"""Gemini on Vertex AI reasoning layer.

All structured calls go through `parse_json()` so slightly malformed model
output (code fences, prose before JSON, list-valued fields) never crashes
the app. Cloud/model failures raise `AIServiceError` with a clear message;
they are surfaced in the UI, not hidden.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from .config import CONFIG
from .models import SourceChunk, WorkAction

load_dotenv()
log = logging.getLogger("workpilot.ai")

MODE_DEMO = "Demo synthesis (no Gemini call)"
MODE_GEMINI = "Gemini on Vertex AI"
MODE_ADK = "Gemini on Vertex AI orchestrated by ADK"
MODE_CACHED = "Cached demo brief (FALLBACK — Gemini unavailable)"
MODE_NO_EVIDENCE = "Not answered — no workspace evidence"
NOT_FOUND = "Not found in workspace sources"


class AIServiceError(RuntimeError):
    """Raised when Gemini on Vertex AI cannot serve a request."""


def _context(chunks: List[SourceChunk]) -> str:
    return "\n\n".join(f"SOURCE: {c.source}\n{c.text}" for c in chunks)


def _client():
    from google import genai

    try:
        from google.genai import types

        return genai.Client(
            vertexai=True,
            project=CONFIG.project_id,
            location=CONFIG.vertex_location,
            http_options=types.HttpOptions(timeout=90_000),
        )
    except Exception:
        return genai.Client(vertexai=True, project=CONFIG.project_id, location=CONFIG.vertex_location)


def generate(prompt: str, json_mode: bool = False) -> str:
    """Single Gemini call. Raises AIServiceError on failure."""
    if CONFIG.demo_mode:
        raise AIServiceError("DEMO_MODE=true: Gemini is not called")
    if not CONFIG.project_id:
        raise AIServiceError("GOOGLE_CLOUD_PROJECT is not set")
    try:
        client = _client()
        config = {"response_mime_type": "application/json"} if json_mode else None
        response = client.models.generate_content(model=CONFIG.gemini_model, contents=prompt, config=config)
        text = response.text or ""
        if not text.strip():
            raise AIServiceError("Gemini returned an empty response")
        return text
    except AIServiceError:
        raise
    except Exception as exc:
        raise AIServiceError(f"Gemini on Vertex AI error: {type(exc).__name__}: {str(exc)[:300]}") from exc


_VERIFIED: Optional[Tuple[bool, str]] = None


def verify(force: bool = False) -> Tuple[bool, str]:
    """One cached round-trip to Gemini so the status view reflects reality."""
    global _VERIFIED
    if _VERIFIED is not None and not force:
        return _VERIFIED
    if CONFIG.demo_mode:
        _VERIFIED = (False, "DEMO_MODE=true")
        return _VERIFIED
    try:
        text = generate("Reply with the single word OK.")
        _VERIFIED = (True, f"{CONFIG.gemini_model} responded ({text.strip()[:20]})")
    except AIServiceError as exc:
        _VERIFIED = (False, str(exc)[:200])
    return _VERIFIED


def status() -> Tuple[str, str]:
    if CONFIG.demo_mode:
        return "FALLBACK", "DEMO_MODE: deterministic demo synthesis, no Gemini calls"
    ok, detail = verify()
    return ("ACTIVE", f"{CONFIG.gemini_model} @ {CONFIG.vertex_location}") if ok else ("ERROR", detail)


def parse_json(raw: str) -> Any:
    """Parse JSON from model output, tolerating fences and surrounding prose."""
    if raw is None:
        raise ValueError("No content to parse")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        raise ValueError("Model output did not contain JSON")
    start = min(starts)
    closer = "]" if text[start] == "[" else "}"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("Model output contained unterminated JSON")
    return json.loads(text[start : end + 1])


# --------------------------------------------------------------------------
# Use case 1: knowledge assistant → structured decision
# --------------------------------------------------------------------------
DECISION_SCHEMA_HINT = """{
  "answer_found": true,
  "finding": "one-paragraph answer to the question grounded in the evidence",
  "blockers": [
    {"blocker": "...", "evidence": ["<source filename>", "..."], "business_impact": "...",
     "recommendation": "...", "owner": "name or Unassigned", "priority": "Critical|High|Medium|Low", "due_date": "date or TBD"}
  ],
  "recommendation": "what to prioritize this week, in priority order",
  "rationale": "why this recommendation follows from the evidence",
  "expected_outcome": "measurable outcome if the recommendation is followed",
  "readiness_status": "NOT READY | AT RISK | READY FOR APPROVAL",
  "approval_conditions": ["condition that must be true before human go-live approval"],
  "open_questions": ["a question a human must answer because the evidence does not; [] if none"]
}"""

DECISION_INSTRUCTION = """You are WorkPilot AI, an enterprise Decision-to-Outcome agent.
Use only the supplied evidence. For go-live questions, return a structured readiness view:
status, blockers with owner/due/evidence, recommended priority, and approval conditions.
Cite source filenames exactly as given. Do not invent facts, owners, or dates.
If the evidence does not answer the question, set "answer_found": false, return no blockers,
and do not guess. open_questions are only questions for the release manager; never comment on
excerpts, truncation, or yourself. List blockers in the order they should be worked.
Humans approve consequential actions. Never mark a production release as approved."""


def not_found_decision(reason: str = "") -> Dict[str, Any]:
    """No evidence, no answer: nothing is generated and no blockers are invented."""
    return {
        "answer_found": False,
        "finding": reason or NOT_FOUND,
        "blockers": [],
        "recommendation": "",
        "rationale": "",
        "expected_outcome": "",
        "readiness_status": "",
        "approval_conditions": [],
        "open_questions": [],
        "missing_information": "",
        "proposed_actions": [],
    }


def _demo_decision(question: str, chunks: List[SourceChunk]) -> Dict[str, Any]:
    if not chunks:
        return not_found_decision()
    sources = list(dict.fromkeys(c.source for c in chunks))
    text = " ".join(c.text for c in chunks).lower()
    blockers = []
    for pattern in _DEMO_PATTERNS:
        if pattern["owner"].lower() in text:
            blockers.append(
                {
                    "blocker": pattern["blocker"],
                    "evidence": [pattern["source"]],
                    "business_impact": pattern["impact"],
                    "recommendation": pattern["title"],
                    "owner": pattern["owner"],
                    "priority": pattern["priority"],
                    "due_date": pattern["due_date"],
                }
            )
    if not blockers:
        return not_found_decision()
    return {
        "answer_found": True,
        "finding": f"Demo synthesis over {', '.join(sources)}: Customer Portal v12.4 is not ready for go-live. Three approval conditions remain open.",
        "blockers": blockers,
        "recommendation": "1) UAT defect closure (PAY-482) 2) Security validation (SEC-217) 3) Performance retest (p95 < 1.5s).",
        "rationale": "Three approval conditions remain incomplete. Architecture has no blocker; verification is the gate.",
        "expected_outcome": "Go-live returns to READY FOR APPROVAL only after SEC-217, PAY-482, and p95 < 1.5s are verified.",
        "readiness_status": "NOT READY",
        "approval_conditions": [
            "SEC-217 verified closed",
            "PAY-482 closed and UAT signed off",
            "Customer Profile API p95 < 1.5 seconds",
        ],
        "open_questions": [],
        "missing_information": "",
    }


def analyze_decision(question: str, chunks: List[SourceChunk]) -> Tuple[Dict[str, Any], str]:
    """Return (structured decision, ai_mode): one direct Gemini call, cached demo if Vertex fails.

    This is the deterministic pipeline the ADK agent falls back to (workpilot.agent.orchestrator).
    """
    if not chunks:
        return not_found_decision(), MODE_NO_EVIDENCE
    if CONFIG.demo_mode:
        return _demo_decision(question, chunks), MODE_DEMO

    try:
        evidence = _context(chunks)
        prompt = f"""{DECISION_INSTRUCTION}

Return ONLY valid JSON matching this shape:
{DECISION_SCHEMA_HINT}

QUESTION:
{question}

EVIDENCE:
{evidence}
"""
        raw = generate(prompt, json_mode=True)
        try:
            data = parse_json(raw)
        except ValueError as exc:
            raise AIServiceError(f"Gemini returned unparseable JSON: {exc}") from exc
        return normalize_decision(data), MODE_GEMINI
    except AIServiceError as exc:
        log.warning("Gemini unavailable, using cached demo brief: %s", exc)
        cached = _demo_decision(question, chunks)
        cached["fallback_reason"] = str(exc)[:300]  # shown with the FALLBACK warning, never as an open question
        return cached, MODE_CACHED


def _item_text(item: Any) -> str:
    """A condition or question as one sentence, whether the model sent a string or an object."""
    if isinstance(item, dict):
        for key in ("condition", "question", "text", "description", "title", "name"):
            if str(item.get(key) or "").strip():
                return str(item[key]).strip()
        return ""
    return str(item or "").strip()


def normalize_decision(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {"finding": str(data)}
    if data.get("answer_found") is False:
        return not_found_decision(str(data.get("finding") or "").strip())
    blockers = []
    for item in data.get("blockers") or []:
        if not isinstance(item, dict):
            continue
        ev = item.get("evidence") or item.get("sources") or []
        if isinstance(ev, str):
            ev = [ev]
        blockers.append(
            {
                "blocker": str(item.get("blocker") or item.get("title") or ""),
                "evidence": [str(e) for e in ev],
                "business_impact": str(item.get("business_impact") or item.get("impact") or ""),
                "recommendation": str(item.get("recommendation") or item.get("action") or ""),
                "owner": str(item.get("owner") or "Unassigned"),
                "priority": str(item.get("priority") or "Medium").capitalize(),
                "due_date": str(item.get("due_date") or item.get("due") or "TBD"),
            }
        )
    return {
        "finding": str(data.get("finding") or data.get("answer") or ""),
        "blockers": blockers,
        "recommendation": str(data.get("recommendation") or ""),
        "rationale": str(data.get("rationale") or ""),
        "expected_outcome": str(data.get("expected_outcome") or ""),
        "missing_information": str(data.get("missing_information") or ""),
        "open_questions": [q for q in (_item_text(q) for q in data.get("open_questions") or []) if q],
        "answer_found": True,
        "readiness_status": str(data.get("readiness_status") or data.get("status") or "").strip(),
        "approval_conditions": [c for c in (_item_text(c) for c in data.get("approval_conditions") or []) if c],
        "proposed_actions": [a for a in (data.get("proposed_actions") or []) if isinstance(a, dict)],
    }


def decision_to_markdown(decision: Dict[str, Any]) -> str:
    lines = [f"**Finding:** {decision.get('finding', '')}", ""]
    for i, b in enumerate(decision.get("blockers") or [], 1):
        lines.append(f"**Blocker {i}: {b['blocker']}**")
        lines.append(f"- Evidence: {', '.join(b['evidence']) or 'n/a'}")
        lines.append(f"- Business impact: {b['business_impact']}")
        lines.append(f"- Recommendation: {b['recommendation']}")
        lines.append(f"- Owner: {b['owner']} · Priority: {b['priority']} · Due: {b['due_date']}")
        lines.append("")
    lines.append(f"**Recommendation this week:** {decision.get('recommendation', '')}")
    lines.append("")
    lines.append(f"**Rationale:** {decision.get('rationale', '')}")
    if decision.get("expected_outcome"):
        lines.append("")
        lines.append(f"**Expected outcome:** {decision['expected_outcome']}")
    if decision.get("missing_information"):
        lines.append("")
        lines.append(f"_Missing information: {decision['missing_information']}_")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Action Contracts
# --------------------------------------------------------------------------
_DEMO_PATTERNS = [
    dict(title="Retest PAY-482 and complete UAT sign-off", owner="Priya", due_date="11 Oct 2026", priority="Critical",
         rationale="UAT sign-off is pending defect verification", source="uat_meeting_notes.md",
         expected_outcome="UAT sign-off completed", blocker="UAT defect verification PAY-482",
         impact="Go-live cannot be described as UAT-complete"),
    dict(title="Validate SEC-217 remediation", owner="Mei", due_date="12 Oct 2026", priority="High",
         rationale="Session token expiration is inconsistent across two services", source="security_assessment.md",
         expected_outcome="Security finding verified closed", blocker="Security validation SEC-217",
         impact="Must be validated before production approval"),
    dict(title="Deploy cache and rerun performance test", owner="Arjun", due_date="13 Oct 2026", priority="High",
         rationale="Customer Profile API p95 is 2.4s against a 1.5s gate", source="performance_test_report.md",
         expected_outcome="p95 latency < 1.5 seconds", blocker="Performance remediation Customer Profile API",
         impact="Release performance gate fails"),
]


def _demo_actions(chunks: List[SourceChunk], decision_id: str) -> List[WorkAction]:
    text = " ".join(c.text for c in chunks).lower()
    actions = []
    for p in _DEMO_PATTERNS:
        if p["owner"].lower() in text or not chunks:
            actions.append(
                WorkAction.from_llm(
                    {k: p[k] for k in ("title", "owner", "due_date", "priority", "rationale", "source", "expected_outcome")},
                    decision_id=decision_id,
                )
            )
    return actions


ACTION_PROMPT = """You are WorkPilot's Action Contract agent.
Extract the most important executable actions from the evidence.
Return ONLY a valid JSON array. Each element has exactly these string fields:
title, owner, due_date, priority, rationale, source, expected_outcome.
Priority must be one of Critical, High, Medium, Low.
"source" is a single filename string (the primary evidence). Never invent owner/date/evidence;
use "Unassigned" or "TBD" if missing. expected_outcome must be measurable when evidence supports one.
Do not create actions that are themselves go-live approval, go/no-go review, or production release
authorization. Those remain human decisions after outcomes are verified.
"""


def extract_actions(chunks: List[SourceChunk], decision_id: str = "", decision: Optional[Dict[str, Any]] = None) -> Tuple[List[WorkAction], str]:
    if CONFIG.demo_mode:
        return _demo_actions(chunks, decision_id), MODE_DEMO

    # The ADK orchestrator may already have proposed actions via ActionTool.
    proposed = (decision or {}).get("proposed_actions") or []
    if proposed:
        actions = parse_actions(proposed, decision_id)
        if actions:
            return actions, MODE_ADK

    guidance = ""
    if decision:
        guidance = f"\nDECISION CONTEXT (align actions to this recommendation):\n{json.dumps(decision, default=str)[:4000]}\n"
    prompt = f"{ACTION_PROMPT}{guidance}\nEVIDENCE:\n{_context(chunks)}\n"
    try:
        raw = generate(prompt, json_mode=True)
        try:
            data = parse_json(raw)
        except ValueError as exc:
            raise AIServiceError(f"Gemini returned unparseable Action Contract JSON: {exc}") from exc
        return parse_actions(data, decision_id), MODE_GEMINI
    except AIServiceError as exc:
        log.warning("Gemini unavailable, using cached demo contracts: %s", exc)
        return _demo_actions(chunks, decision_id), MODE_CACHED


def parse_actions(data: Any, decision_id: str = "") -> List[WorkAction]:
    if isinstance(data, dict):
        data = data.get("actions") or data.get("items") or [data]
    actions: List[WorkAction] = []
    for item in data or []:
        if isinstance(item, dict):
            actions.append(WorkAction.from_llm(item, decision_id=decision_id))
    return actions


# --------------------------------------------------------------------------
# Use case 2: meeting intelligence
# --------------------------------------------------------------------------
MEETING_PROMPT = """You are WorkPilot's Meeting Intelligence agent.
From the meeting notes or transcript below, extract:
- decisions: [{"decision": "...", "rationale": "...", "decided_by": "name or Unassigned"}]
- risks: [{"risk": "...", "impact": "...", "mitigation": "..."}]
- actions: [{"title": "...", "owner": "...", "due_date": "...", "priority": "Critical|High|Medium|Low",
             "rationale": "...", "source": "<source name>", "expected_outcome": "..."}]
Return ONLY valid JSON with keys decisions, risks, actions. Never invent owners or dates; use "Unassigned"/"TBD".
"""


def _demo_meeting(source_name: str, text: str) -> Dict[str, Any]:
    low = text.lower()
    decisions = []
    if "cach" in low:
        decisions.append({"decision": "Proceed with cache optimization before adding compute", "rationale": "Fixes the query pattern instead of masking it with cost", "decided_by": "Arjun"})
    if "go-live" in low or "sign-off" in low:
        decisions.append({"decision": "Do not approve production release until remaining approval conditions are verified", "rationale": "UAT, security, and performance gates are still open", "decided_by": "Sara"})
    if "certification" in low or "sec-217" in low:
        decisions.append({"decision": "Validate SEC-217 before production approval", "rationale": "Security validation is a go-live condition", "decided_by": "Mei"})
    risks = []
    if "security" in low:
        risks.append({"risk": "Open security findings reach production", "impact": "Production deployment blocked or exposed", "mitigation": "Close both findings before deployment"})
    if "uat" in low:
        risks.append({"risk": "UAT slips if data refresh misses Wednesday", "impact": "Sign-off delayed", "mitigation": "Ravi coordinates refresh with data team"})
    chunks = [SourceChunk(source_name, text)]
    actions = [a.to_dict() for a in _demo_actions(chunks, "")]
    return {"decisions": decisions, "risks": risks, "actions": actions}


# --------------------------------------------------------------------------
# Outcome verification assist
# --------------------------------------------------------------------------
def assess_outcome(expected: str, actual: str) -> Tuple[Dict[str, str], str]:
    """Suggest an outcome status. Human confirmation remains mandatory."""
    if CONFIG.demo_mode:
        low = actual.lower()
        status = "ACHIEVED" if any(w in low for w in ("done", "met", "achieved", "complete", "passed")) else "PARTIALLY_ACHIEVED" if actual.strip() else "PENDING"
        return {"suggested_status": status, "reasoning": "Keyword heuristic (DEMO_MODE)."}, MODE_DEMO
    prompt = f"""Compare the expected outcome with the actual outcome evidence.
Return ONLY JSON: {{"suggested_status": "ACHIEVED|PARTIALLY_ACHIEVED|NOT_ACHIEVED|BLOCKED|PENDING", "reasoning": "one sentence"}}
EXPECTED: {expected}
ACTUAL: {actual}
"""
    raw = generate(prompt, json_mode=True)
    try:
        data = parse_json(raw)
    except ValueError:
        data = {}
    status = str(data.get("suggested_status") or "PENDING").upper()
    if status not in {"ACHIEVED", "PARTIALLY_ACHIEVED", "NOT_ACHIEVED", "BLOCKED", "PENDING"}:
        status = "PENDING"
    return {"suggested_status": status, "reasoning": str(data.get("reasoning") or "")}, MODE_GEMINI
