"""WorkPilot Impact: productivity metrics derived from recorded events.

Nothing here is hardcoded. Counts come from Firestore (or the in-memory
fallback) and time estimates come from the configurable assumptions in
`config.IMPACT`, which are displayed next to the numbers.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from . import state_store
from .config import IMPACT, ImpactAssumptions
from .documents import word_count
from .models import ExecutionMode, ExecutionStatus


def _parse_ts(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _within(items: Iterable[Dict[str, Any]], since: datetime, key: str = "created_at") -> List[Dict[str, Any]]:
    return [i for i in items if _parse_ts(i.get(key, "")) >= since]


def compute_impact(
    assumptions: ImpactAssumptions = IMPACT,
    decision_id: str = "",
    documents: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=assumptions.window_days)

    sources = state_store.load_knowledge_sources()
    actions = state_store.load_actions()
    decisions = [d.to_dict() for d in state_store.load_decisions(limit=500)]
    executions = [e.to_dict() for e in state_store.load_executions(limit=500)]
    events = state_store.load_events(limit=1000)
    if decision_id:
        actions = [a for a in actions if a.get("decision_id") == decision_id]
        decisions = [d for d in decisions if d.get("decision_id") == decision_id]
        executions = [e for e in executions if e.get("decision_id") == decision_id]
        events = [e for e in events if e.get("decision_id") == decision_id]
    if documents is not None:
        names = set(documents)
        sources = [s for s in sources if str(s.get("name") or "") in names]
        if len(sources) < len(names):
            sources = [
                {"name": name, "word_count": word_count(text), "updated_at": now.isoformat()}
                for name, text in documents.items()
            ]

    # --- counts (all time) -------------------------------------------------
    unique_sources = {s.get("name") or s.get("source_id") for s in sources if s.get("name") or s.get("source_id")}
    total_words = sum(int(s.get("word_count", 0) or 0) for s in sources)

    identified_from_events = sum(
        int((e.get("payload") or {}).get("count", 0) or 0) for e in events if e.get("event_type") == "action_identified"
    )
    actions_prepared = len(actions)
    potential_actions = max(identified_from_events, actions_prepared)

    decisions_supported = len(decisions)
    successful_execs = [e for e in executions if e.get("status") == ExecutionStatus.SUCCESS]
    followups_real = sum(1 for e in successful_execs if e.get("mode") == ExecutionMode.REAL)
    followups_demo = len(successful_execs) - followups_real

    approved = sum(1 for a in actions if a.get("status") not in ("PROPOSED", "REJECTED"))
    verified = sum(1 for a in actions if a.get("status") == "VERIFIED")

    blockers_from_events = sum(
        int((e.get("payload") or {}).get("count", 0) or 0)
        for e in events
        if e.get("event_type") == "blocker_identified"
    )
    owners = {str(a.get("owner") or "").strip() for a in actions if str(a.get("owner") or "").strip() and str(a.get("owner")).lower() != "unassigned"}
    blockers_consolidated = max(blockers_from_events, len(owners), 0)

    # --- time estimates (trailing window → "weekly") ----------------------
    window_actions = _within(actions, since)
    window_decisions = _within(decisions, since)
    window_execs = _within(successful_execs, since)
    window_sources = _within(sources, since, key="updated_at")
    window_words = sum(int(s.get("word_count", 0) or 0) for s in window_sources)
    window_owners = {str(a.get("owner") or "").strip() for a in window_actions if str(a.get("owner") or "").strip() and str(a.get("owner")).lower() != "unassigned"}

    reading_minutes = window_words / assumptions.reading_words_per_minute if assumptions.reading_words_per_minute else 0.0
    action_minutes = len(window_actions) * assumptions.minutes_per_prepared_action
    followup_minutes = len(window_execs) * assumptions.minutes_per_followup
    decision_minutes = len(window_decisions) * assumptions.minutes_per_decision
    blocker_minutes = blockers_consolidated * assumptions.minutes_per_blocker
    coordination_minutes = len(window_owners) * assumptions.minutes_per_owner_coordination
    total_minutes = reading_minutes + action_minutes + decision_minutes + blocker_minutes + coordination_minutes

    all_time_reading_minutes = total_words / assumptions.reading_words_per_minute if assumptions.reading_words_per_minute else 0.0

    return {
        "computed_at": now.isoformat(),
        "window_days": assumptions.window_days,
        "scope": "decision" if decision_id else "workspace",
        "decision_id": decision_id,
        "metrics": {
            "knowledge_sources_analyzed": len(unique_sources),
            "estimated_manual_reading_avoided_hours": round(all_time_reading_minutes / 60, 2),
            "potential_actions_identified": potential_actions,
            "actions_automatically_prepared": actions_prepared,
            "decisions_supported": decisions_supported,
            "release_blockers_consolidated": blockers_consolidated,
            "coordination_effort_avoided_minutes": round(coordination_minutes, 1),
            "followups_automated": len(successful_execs),
            "followups_real": followups_real,
            "followups_demo": followups_demo,
            "actions_approved": approved,
            "outcomes_verified": verified,
            "estimated_weekly_time_saved_hours": round(total_minutes / 60, 2),
            "estimated_total_time_saved_hours": round(total_minutes / 60, 2),
            "steps_removed": int(
                bool(unique_sources)
                + bool(decisions_supported)
                + bool(actions_prepared)
                + bool(approved)
                + bool(verified)
            ),
        },
        "per_task": [
            {
                "title": str(action.get("title") or ""),
                "owner": str(action.get("owner") or "Unassigned"),
                "source": str(action.get("source") or ""),
                "status": str(action.get("status") or ""),
                "estimated_minutes": assumptions.minutes_per_prepared_action,
            }
            for action in actions
        ],
        "breakdown_minutes": {
            "reading": round(reading_minutes, 1),
            "action_preparation": round(action_minutes, 1),
            "decision_summarization": round(decision_minutes, 1),
            "blocker_analysis": round(blocker_minutes, 1),
            "coordination": round(coordination_minutes, 1),
            "followups": round(followup_minutes, 1),
        },
        "inputs": {
            "total_words_analyzed": total_words,
            "window_words_analyzed": window_words,
            "window_actions": len(window_actions),
            "window_decisions": len(window_decisions),
            "window_followups": len(window_execs),
            "unique_owners": len(owners),
            "blockers_consolidated": blockers_consolidated,
        },
        "assumptions": {
            "reading_words_per_minute": assumptions.reading_words_per_minute,
            "minutes_per_prepared_action": assumptions.minutes_per_prepared_action,
            "minutes_per_followup": assumptions.minutes_per_followup,
            "minutes_per_decision": assumptions.minutes_per_decision,
            "minutes_per_blocker": assumptions.minutes_per_blocker,
            "minutes_per_owner_coordination": assumptions.minutes_per_owner_coordination,
            "window_days": assumptions.window_days,
        },
    }


METHODOLOGY = """
**How these numbers are calculated**

- **Knowledge sources analyzed** — unique documents in the current workspace view (the same files shown on Know).
- **Estimated manual reading avoided** — total words in those sources ÷ {wpm:g} words per minute.
- **Potential actions identified** — action candidates surfaced by analysis (`action_identified` events), never less than the contracts prepared.
- **Action Contracts prepared** — Action Contracts persisted by WorkPilot (`actions`).
- **Decisions supported** — DecisionRecords created (`decisions`).
- **Release blockers consolidated** — unique blockers / owners taken from analysis events, not a hardcoded count.
- **Coordination effort avoided** — unique action owners × {coord:g} minutes.
- **Steps removed** — count of completed copilot hops in scope (sources consulted, brief, contracts, approvals, verified outcomes). Not a stopwatch.
- **Per-task minutes** — {act:g} min assumed per prepared Action Contract.
- **Estimated total time saved** — over the trailing {days} days:
  reading avoided + {act:g} min × actions prepared + {dec:g} min × decisions + {blk:g} min × blockers + coordination.

These are estimates based on the stated assumptions, not measured user time. Assumptions are configurable via
`IMPACT_READING_WPM`, `IMPACT_MINUTES_PER_ACTION`, `IMPACT_MINUTES_PER_DECISION`, `IMPACT_MINUTES_PER_BLOCKER`, `IMPACT_MINUTES_PER_OWNER`, `IMPACT_WINDOW_DAYS`.
"""


def methodology_markdown(assumptions: ImpactAssumptions = IMPACT) -> str:
    return METHODOLOGY.format(
        wpm=assumptions.reading_words_per_minute,
        act=assumptions.minutes_per_prepared_action,
        dec=assumptions.minutes_per_decision,
        blk=assumptions.minutes_per_blocker,
        coord=assumptions.minutes_per_owner_coordination,
        days=assumptions.window_days,
    )
