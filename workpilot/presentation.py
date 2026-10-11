"""UI-facing helpers (view models for the API). No Google calls.

Single source of truth for the five-step workflow: selected step is independent
of completion. Completion is derived from persisted or session-recorded work,
never from merely opening a panel.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import execution, scenario
from .config import CONFIG
from .models import ActionStatus, ExecutionStatus, OutcomeStatus

STEPS = ("Know", "Decide", "Approve", "Act", "Measure")


CHALLENGE_NAME = "Future of Work & Enterprise Productivity"
VALUE_PROP = (
    "WorkPilot turns enterprise documents into an approved go-live decision — "
    "with citations, a human gate, and estimated time saved."
)
FIRST_HEADLINE = VALUE_PROP
FIRST_SUBHEAD = scenario.PERSONA_LINE


# Cloud Run revision names (service-00037-xn7) identify the deployment, not the product.
_REVISION = re.compile(r"\b[a-z][a-z0-9-]*-\d{5}-[a-z0-9]{3}\b")
_APPROVED_STATUSES = {
    ActionStatus.APPROVED,
    ActionStatus.IN_PROGRESS,
    ActionStatus.BLOCKED,
    ActionStatus.COMPLETED,
    ActionStatus.FAILED,
    ActionStatus.VERIFIED,
}


def followups_connected() -> bool:
    """True only when a live Gmail/Calendar adapter is verified."""
    return bool(execution.real_available())


def session_actions(
    actions: Sequence[Dict[str, Any]],
    decision_id: str = "",
    action_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Primary board for this browser session. Never dumps the shared workspace."""
    if decision_id:
        matched = [a for a in actions if a.get("decision_id") == decision_id]
        if matched:
            return matched
    allowed = {i for i in (action_ids or []) if i}
    if allowed:
        return [a for a in actions if a.get("action_id") in allowed]
    return []


def board_progress(actions: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Counts derived from this session's Action Contracts. Single source for every status label."""
    board = list(actions or [])
    live = [a for a in board if ActionStatus.normalize(a.get("status")) != ActionStatus.REJECTED]
    return {
        "total": len(board),
        "pending": sum(1 for a in board if ActionStatus.normalize(a.get("status")) == ActionStatus.PROPOSED),
        "approved": sum(1 for a in board if ActionStatus.normalize(a.get("status")) in _APPROVED_STATUSES),
        "rejected": len(board) - len(live),
        "executed": sum(1 for a in board if a.get("execution_status") == ExecutionStatus.EXECUTED),
        "measurable": len(live),
        "measured": sum(1 for a in live if str(a.get("outcome_status") or OutcomeStatus.PENDING) != OutcomeStatus.PENDING),
    }


def completion_flags(
    *,
    has_retrieval: bool,
    has_analysis: bool,
    approval_complete: bool,
    has_executed: bool,
    has_measured_outcome: bool,
) -> Dict[str, bool]:
    """A step is Completed only when its work is done; opening it never counts."""
    return {
        "Know": bool(has_retrieval or has_analysis),
        "Decide": bool(has_analysis),
        "Approve": bool(approval_complete),
        "Act": bool(has_executed),
        "Measure": bool(has_measured_outcome),
    }


def flags_from_board(*, has_retrieval: bool, has_analysis: bool, actions: Sequence[Dict[str, Any]]) -> Dict[str, bool]:
    progress = board_progress(actions)
    return completion_flags(
        has_retrieval=has_retrieval,
        has_analysis=has_analysis,
        approval_complete=progress["total"] > 0 and progress["pending"] == 0,
        has_executed=progress["executed"] > 0,
        has_measured_outcome=progress["measured"] > 0,
    )


def has_measured_outcome(actions: Sequence[Dict[str, Any]]) -> bool:
    return board_progress(actions)["measured"] > 0


def approval_label(status: str) -> str:
    value = ActionStatus.normalize(status)
    if value == ActionStatus.PROPOSED:
        return "Pending"
    if value == ActionStatus.REJECTED:
        return "Rejected"
    if value in _APPROVED_STATUSES:
        return "Approved"
    return value


def execution_label(action: Dict[str, Any]) -> str:
    """Executed only when a live adapter returned an ID; everything else is explicit."""
    stored = str(action.get("execution_status") or ExecutionStatus.NOT_STARTED)
    if stored == ExecutionStatus.EXECUTED:
        return "Executed"
    if stored == ExecutionStatus.FAILED:
        return "Failed"
    return "Not started"


def nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    text = str(value).strip()
    return bool(text) and text.upper() not in {"TBD", "N/A", "NONE", "UNASSIGNED"}


def brief_sections(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Only fields the model actually returned. Nothing is invented here."""
    blockers = []
    for raw in decision.get("blockers") or []:
        if not isinstance(raw, dict):
            continue
        item = {
            "blocker": str(raw.get("blocker") or "").strip(),
            "business_impact": str(raw.get("business_impact") or "").strip(),
            "recommendation": str(raw.get("recommendation") or "").strip(),
            "owner": str(raw.get("owner") or "").strip(),
            "due_date": str(raw.get("due_date") or "").strip(),
            "priority": str(raw.get("priority") or "").strip(),
            "evidence": [str(e) for e in (raw.get("evidence") or []) if str(e).strip()],
        }
        if item["blocker"] or item["business_impact"] or item["recommendation"]:
            blockers.append(item)
    return {
        "finding": str(decision.get("finding") or "").strip(),
        "recommendation": str(decision.get("recommendation") or "").strip(),
        "recommendation_items": recommendation_items(blockers),
        "rationale": str(decision.get("rationale") or "").strip(),
        "expected_outcome": str(decision.get("expected_outcome") or "").strip(),
        "missing_information": str(decision.get("missing_information") or "").strip(),
        "open_questions": open_questions(decision),
        "readiness_status": str(decision.get("readiness_status") or "").strip(),
        "approval_conditions": [
            str(c).strip() for c in (decision.get("approval_conditions") or []) if str(c).strip()
        ],
        "blockers": blockers,
    }


def recommendation_items(blockers: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One ordered item per blocker, from structured JSON rather than a prose list.

    Each item carries the blocker it resolves, so the brief shows every blocker
    once instead of repeating it as a recommendation, a risk card and a pack.
    """
    items = []
    for b in blockers or []:
        text = str(b.get("recommendation") or b.get("blocker") or "").strip()
        if not text:
            continue
        blocker = str(b.get("blocker") or "").strip()
        items.append(
            {
                "text": text,
                "blocker": blocker if blocker and blocker != text else "",
                "impact": str(b.get("business_impact") or "").strip(),
                "priority": str(b.get("priority") or "").strip(),
                "evidence": [str(e) for e in (b.get("evidence") or []) if str(e).strip()],
                "owner": str(b.get("owner") or "").strip() if nonempty(b.get("owner")) else "",
                "due_date": str(b.get("due_date") or "").strip() if nonempty(b.get("due_date")) else "",
            }
        )
    return items


def hours_label(hours: Any) -> str:
    """Readings under an hour read better as minutes: 0.13 hrs → 8 min."""
    try:
        value = float(hours or 0)
    except (TypeError, ValueError):
        return str(hours)
    if value < 1:
        return f"{round(value * 60)} min"
    return f"{value:.1f} hrs"


# Notes about the model, the excerpts, or the pipeline are not questions for a human.
_MODEL_NOTE = re.compile(
    r"truncat|cut off|cut-off|excerpt|snippet|chunk|provided (text|evidence|context|documents?|sources?)"
    r"|supplied evidence|\bdemo\b|cached|gemini|\bmodel\b|demo_mode|fallback|no gemini call",
    re.I,
)


def open_questions(decision: Dict[str, Any]) -> List[str]:
    raw = decision.get("open_questions")
    if isinstance(raw, (list, tuple)):
        candidates = [str(q).strip() for q in raw]
    else:
        text = str(decision.get("missing_information") or "").strip()
        candidates = [part.strip() for part in re.split(r"(?<=[.?!])\s+|\n+|;\s+", text)]
    out = []
    for item in candidates:
        item = re.sub(r"^[-*•\d.)\s]+", "", item).strip()
        if len(item) < 8 or _MODEL_NOTE.search(item):
            continue
        out.append(item)
    return out


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b"),
)


def parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    m = _DATE_PATTERNS[0].search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATE_PATTERNS[1].search(text)
    if m:
        month = _MONTHS.get(m.group(2)[:3].lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                return None
    return None


def fmt_date(value: date) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


def due_label(due: Any, today: Optional[date] = None) -> str:
    """"Due today", "Overdue by 2 days", "Due in 3 days", or "" when unparseable."""
    parsed = parse_date(due)
    if parsed is None:
        return ""
    delta = (parsed - (today or date.today())).days
    if delta == 0:
        return "Due today"
    if delta < 0:
        return f"Overdue by {-delta} day{'s' if delta != -1 else ''}"
    return f"Due in {delta} day{'s' if delta != 1 else ''}"


_AS_OF = re.compile(r"\*{0,2}(?:date|export date|as of)\*{0,2}\s*:?\*{0,2}\s*([^\n]+)", re.I)


def evidence_as_of(sources: Iterable[str], documents: Dict[str, str]) -> Optional[date]:
    """Newest document date among the cited sources (the brief cannot be fresher)."""
    found = []
    for name in dict.fromkeys(sources or []):
        text = documents.get(name) or ""
        for match in _AS_OF.finditer(text[:1500]):
            parsed = parse_date(match.group(1))
            if parsed:
                found.append(parsed)
                break
    return max(found) if found else None


# Only a fully achieved outcome closes a condition; "partially achieved" leaves it open.
_CLOSED_OUTCOMES = {OutcomeStatus.ACHIEVED}


def verdict(decision: Optional[Dict[str, Any]], actions: Sequence[Dict[str, Any]] = ()) -> Optional[Dict[str, Any]]:
    """Large go-live verdict. Counts come from approval conditions, then real outcomes."""
    payload = decision or {}
    if not payload or payload.get("answer_found") is False:
        return None
    live = [a for a in actions or [] if ActionStatus.normalize(a.get("status")) != ActionStatus.REJECTED]
    open_actions = sum(1 for a in live if str(a.get("outcome_status") or "") not in _CLOSED_OUTCOMES)
    closed_actions = len(live) - open_actions
    conditions = [c for c in payload.get("approval_conditions") or [] if str(c).strip()]
    if conditions:
        # The denominator stays the brief's approval conditions from Decide to Measure.
        # Contracts do not map 1:1 to conditions, so a condition closes only through a
        # verified outcome, and any still-open contract keeps at least one condition open.
        total = len(conditions)
        open_n = max(total - min(closed_actions, total), min(total, open_actions))
        noun = "approval conditions"
    elif live:
        total, open_n, noun = len(live), open_actions, "release actions"
    else:
        total = len([b for b in payload.get("blockers") or [] if isinstance(b, dict)])
        open_n, noun = total, "approval conditions"
    if total == 0:
        status = str(payload.get("readiness_status") or "").strip().upper() or "NOT READY"
    elif open_n == 0:
        status = "READY FOR APPROVAL"
    elif open_n < total:
        status = "AT RISK"
    else:
        status = "NOT READY"
    tone = {"READY FOR APPROVAL": "green", "AT RISK": "amber"}.get(status, "red")
    if total:
        headline = f"{status} — {open_n} of {total} {noun} open"
    else:
        headline = status
    return {"status": status, "tone": tone, "open": open_n, "total": total, "headline": headline}


def evidence_cards(hits: Sequence[Any], limit: int = 6) -> List[Dict[str, str]]:
    cards = []
    for rank, hit in enumerate(hits, start=1):
        get = (lambda k, d=None: hit.get(k, d)) if isinstance(hit, dict) else (lambda k, d=None: getattr(hit, k, d))
        source = get("source") or ""
        text = get("snippet") or get("text") or get("excerpt") or ""
        page = get("page") or 1
        section = str(get("section") or "").strip()
        if not source and not text:
            continue
        cards.append(
            {
                "source": str(source),
                # A passage cut from a list should not open with "2. ".
                "excerpt": re.sub(r"^\d+[.)]\s+", "", clean_excerpt(text))[:700],
                "page": str(int(page or 1)),
                "locator": section or f"section {int(page or 1)}",
                "section": section,
                "score": f"{float(get('score') or 0.0):.2f}",
                "rank": str(rank),
            }
        )
        if len(cards) >= limit:
            break
    return cards


def snippet(text: str, limit: int = 420) -> str:
    """Trim on a word boundary so a snippet never ends mid-word."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:·-")
    return cut + "…"


RETRIEVAL_ACTIVE = "Vertex AI Search (ACTIVE)"
RETRIEVAL_FALLBACK = "Local TF-IDF (FALLBACK)"
PROVIDER_VERTEX = "Vertex AI Search"
PROVIDER_LOCAL = "Local TF-IDF"


def provider_badge(last_mode: str = "") -> str:
    """Short name of the retrieval provider that actually ran. Never says FALLBACK."""
    mode = str(last_mode or "").upper()
    if "VERTEX" in mode and "FALLBACK" not in mode:
        return PROVIDER_VERTEX
    return PROVIDER_LOCAL


def retrieval_mode_label(last_mode: str = "", search_status: str = "") -> str:
    """Honest retrieval label for Know and Architecture. Never advertise unused Search."""
    mode = str(last_mode or "").strip()
    status = str(search_status or "").strip().upper()
    if mode:
        if "VERTEX" in mode.upper() and "FALLBACK" not in mode.upper():
            return RETRIEVAL_ACTIVE
        return RETRIEVAL_FALLBACK
    if status == "ACTIVE":
        return RETRIEVAL_ACTIVE
    return RETRIEVAL_FALLBACK


_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


def clean_excerpt(text: str) -> str:
    """Strip markdown headings, emphasis, bullets and table pipes so evidence reads as a snippet."""
    parts = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or _TABLE_RULE.match(line):
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            line = " · ".join(c for c in cells if c)
        line = re.sub(r"(^|\s)#{1,6}\s+", r"\1", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"[*_`]+", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            parts.append(line)
    return " ".join(parts)


def _redact_detail(detail: str) -> str:
    """Hide project and resource identifiers and account emails; keep the message readable.

    Only the configured identifiers are replaced, so words such as "processor" or
    "documentai.processors.get" in an error stay intact.
    """
    text = str(detail or "")
    labels = (
        (CONFIG.project_id, "this Cloud project"),
        (CONFIG.document_ai_processor_id, "the configured processor"),
        (CONFIG.vertex_search_engine_id, "the configured engine"),
        (CONFIG.vertex_search_data_store_id, "the configured data store"),
    )
    for value, label in labels:
        value = (value or "").strip()
        if len(value) >= 4:
            text = text.replace(value, label)
    # The connected Gmail/Calendar account is personal data; never show it publicly.
    text = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "the connected Google account", text)
    text = re.sub(r"\b\d{12}\b", "this Cloud project", text)
    text = re.sub(r"\bproject-[a-z0-9]+(?:-[a-z0-9]+)+\b", "this Cloud project", text)
    text = _REVISION.sub("the current revision", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def public_stack_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    """Honest labels for the jury, with project identifiers redacted."""
    out = []
    for row in rows:
        item = dict(row)
        item["detail"] = _redact_detail(item.get("detail", ""))
        out.append(item)
    return out


_SOURCE_TITLES = {
    "uat_meeting_notes": "UAT Meeting Notes",
}


def friendly_source_name(name: str) -> str:
    stem = Path(str(name or "")).stem
    key = stem.replace("-", "_").replace(" ", "_").lower()
    if key in _SOURCE_TITLES:
        return _SOURCE_TITLES[key]
    return stem.replace("_", " ").replace("-", " ").title()


# --------------------------------------------------------------------------
# Decision brief layout: key findings and the decision record
# --------------------------------------------------------------------------
_ISSUE_ID = re.compile(r"\b[A-Z][A-Z0-9]{1,7}-\d{1,6}\b")


def issue_ids(text: str) -> List[str]:
    """Tracker IDs named in the text (SEC-217, PAY-482), in order, without repeats."""
    return list(dict.fromkeys(_ISSUE_ID.findall(str(text or ""))))


def initials(name: str) -> str:
    """Avatar letters for an owner; empty for Unassigned/TBD."""
    if not nonempty(name):
        return ""
    words = [w for w in re.split(r"[\s._-]+", str(name)) if w and w[0].isalpha()]
    return "".join(w[0] for w in words[:2]).upper()


def _short_label(blocker: Dict[str, Any]) -> str:
    ids = issue_ids(f"{blocker.get('blocker', '')} {blocker.get('recommendation', '')}")
    if ids:
        return ids[0]
    words = str(blocker.get("blocker") or blocker.get("recommendation") or "").split()
    return " ".join(words[:4]) + ("…" if len(words) > 4 else "")


def evidence_map(blockers: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each cited source with the blockers it supports, in the order sources are first cited."""
    by_source: Dict[str, List[str]] = {}
    for blocker in blockers or []:
        label = _short_label(blocker)
        for source in blocker.get("evidence") or []:
            labels = by_source.setdefault(str(source), [])
            if label and label not in labels:
                labels.append(label)
    return [{"source": source, "supports": labels} for source, labels in by_source.items()]


def brief_stats(sections: Dict[str, Any]) -> Dict[str, int]:
    """Counts shown beside the verdict, all from the recorded decision."""
    blockers = sections.get("blockers") or []
    return {
        "blockers": len(blockers),
        "urgent": sum(1 for b in blockers if str(b.get("priority") or "").capitalize() in {"Critical", "High"}),
        "owners": len({b["owner"] for b in blockers if nonempty(b.get("owner"))}),
        "conditions": len(sections.get("approval_conditions") or []),
    }


# --------------------------------------------------------------------------
# Approve, Measure and Impact layout
# --------------------------------------------------------------------------
def approval_tally(actions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Approve's counts row, from the same board_progress as every other label."""
    p = board_progress(actions)
    return [
        {"label": "Pending", "count": p["pending"], "tone": "proposed"},
        {"label": "Approved", "count": p["approved"], "tone": "progress"},
        {"label": "Rejected", "count": p["rejected"], "tone": "warn"},
        {"label": "Executed", "count": p["executed"], "tone": "done"},
    ]


def outcome_rows(actions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Measure's outcome checklist: one row per non-rejected contract, recorded outcomes only."""
    rows = []
    for a in actions or []:
        if ActionStatus.normalize(a.get("status")) == ActionStatus.REJECTED:
            continue
        outcome = str(a.get("outcome_status") or OutcomeStatus.PENDING)
        if outcome == OutcomeStatus.ACHIEVED:
            state = "done"
        elif outcome == OutcomeStatus.PARTIALLY_ACHIEVED:
            state = "partial"
        elif outcome == OutcomeStatus.PENDING:
            state = "open"
        else:
            state = "missed"
        rows.append(
            {
                "title": str(a.get("title") or "Action"),
                "owner": str(a.get("owner") or "Unassigned"),
                "execution": execution_label(a),
                "expected": str(a.get("expected_outcome") or ""),
                "actual": str(a.get("actual_outcome") or "") if outcome != OutcomeStatus.PENDING else "",
                "outcome": outcome.replace("_", " ").title(),
                "tone": {"done": "done", "partial": "warn", "missed": "urgent"}.get(state, "proposed"),
                "state": state,
            }
        )
    return rows


def readiness_update(decision: Optional[Dict[str, Any]], actions: Sequence[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The banner under the outcome checklist, once at least one outcome is recorded.

    READY FOR APPROVAL is the best it can say: go-live approval itself stays with a person.
    """
    if board_progress(actions)["measured"] == 0:
        return None
    v = verdict(decision, actions)
    if not v:
        return None
    if v["status"] == "READY FOR APPROVAL":
        text = (
            f"All {v['total']} approval conditions are met by recorded outcomes. "
            "Final go-live approval stays with a person."
        )
    else:
        text = f"{v['open']} of {v['total']} still open after the outcomes recorded so far."
    return {"status": v["status"], "tone": v["tone"], "title": f"Release readiness updated · {v['status']}", "text": text}


IMPACT_TILES = (
    ("knowledge_sources_analyzed", "Knowledge sources analysed", "blue", "{:g}"),
    ("estimated_manual_reading_avoided_hours", "Estimated reading avoided", "green", "hours"),
    ("release_blockers_consolidated", "Release blockers consolidated", "violet", "{:g}"),
    ("actions_automatically_prepared", "Action Contracts prepared", "blue", "{:g}"),
    ("decisions_supported", "Decisions supported", "red", "{:g}"),
    ("coordination_effort_avoided_minutes", "Coordination effort avoided", "amber", "{:g} min"),
)


def impact_tiles(metrics: Dict[str, Any]) -> List[Dict[str, str]]:
    """Impact tiles, each a value compute_impact returned; formulas are in the methodology."""
    tiles = []
    for key, label, tone, fmt in IMPACT_TILES:
        value = metrics.get(key)
        if value is None:
            continue
        shown = hours_label(value) if fmt == "hours" else fmt.format(float(value))
        tiles.append({"value": shown, "label": label, "tone": tone})
    return tiles
