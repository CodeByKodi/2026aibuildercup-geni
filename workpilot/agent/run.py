"""One WorkPilot agent run: the shared context its tools act on, and their trace.

Tools are plain Python functions with typed parameters and docstrings, so ADK
can turn them into Gemini function declarations. They are bound to an
`AgentRun` (the session, the retriever, the evidence found so far) rather
than receiving it as an argument, because the model must not choose which
session or retriever it acts on.
"""
from __future__ import annotations

import functools
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..models import SourceChunk
from ..retrieval import EnterpriseRetriever

log = logging.getLogger("workpilot.agent")


@dataclass
class AgentRun:
    session_id: str
    retriever: EnterpriseRetriever
    documents: Dict[str, str]
    question: str = ""
    hits: List[SourceChunk] = field(default_factory=list)
    retrieval_mode: str = ""
    not_found: str = ""
    decision_id: str = ""
    decision: Dict[str, Any] = field(default_factory=dict)
    answer_found: Optional[bool] = None  # what create_decision was told, if it was called
    proposed_outcome: Dict[str, Any] = field(default_factory=dict)  # evaluate_outcome's proposal, never recorded
    readiness: Dict[str, Any] = field(default_factory=dict)  # assess_readiness's computed verdict
    impact: Dict[str, Any] = field(default_factory=dict)  # calculate_impact's computed metrics
    trace: List[Dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)  # when the turn began, for analytics durations
    on_tool: Optional[Callable[[str, bool], None]] = None  # (tool name, finished?) for progress UI

    def evidence_sources(self) -> set:
        return {h.source for h in self.hits}


def traced(run: AgentRun, fn: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
    """Record every tool call in the run's trace; turn failures into messages for the model."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        if run.on_tool:
            run.on_tool(fn.__name__, False)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # the agent gets a message it can act on, never a crash
            log.warning("Tool %s failed: %s", fn.__name__, exc)
            result = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        run.trace.append(
            {
                "tool": fn.__name__,
                "ok": "error" not in result,
                "summary": str(result.get("summary") or result.get("error") or "")[:200],
                "ms": int((time.perf_counter() - started) * 1000),
            }
        )
        if run.on_tool:
            run.on_tool(fn.__name__, True)
        return result

    return wrapper


def elapsed_ms(run: AgentRun) -> int:
    return int((time.perf_counter() - run.started) * 1000)
