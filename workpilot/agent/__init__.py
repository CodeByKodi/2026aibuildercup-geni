"""WorkPilot agent tools (Google ADK).

    WorkPilot Orchestrator
        ├── Knowledge Tool   search_knowledge, get_document
        ├── Decision Tool    create_decision
        ├── Action Tool      create_action_contracts, get_action_status
        ├── Outcome Tool     evaluate_outcome, assess_readiness, calculate_impact
        └── Workflow memory  get_workflow_context

Human approval is a structural boundary: no tool approves, rejects, executes,
or confirms an outcome. Those stay audited clicks by a person in the UI.
"""
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import action, decision, knowledge, memory, outcome
from .run import AgentRun, traced

TOOL_GROUPS = {
    "Knowledge Tool": knowledge,
    "Decision Tool": decision,
    "Action Tool": action,
    "Outcome Tool": outcome,
    "Workflow memory": memory,
}


def build_tools(run: AgentRun, names: Optional[Iterable[str]] = None) -> List[Callable[..., Dict[str, Any]]]:
    """Tools for one run, each bound to it and traced. `names` limits a turn to the tools it needs."""
    wanted = set(names) if names is not None else None
    return [
        traced(run, fn)
        for module in TOOL_GROUPS.values()
        for fn in module.make(run)
        if wanted is None or fn.__name__ in wanted
    ]


__all__ = ["AgentRun", "TOOL_GROUPS", "build_tools"]
