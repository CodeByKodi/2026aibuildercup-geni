"""ADK runtime: runs one WorkPilot Orchestrator turn with the tools it is given.

The turns and their tools live in `workpilot.agent`. This module only knows how
to run ADK on Vertex AI and remembers what the last turn did, so System Status
reports the path that actually ran. If ADK is unavailable the caller falls back
to the deterministic pipeline.
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG
from .models import utc_now

log = logging.getLogger("workpilot.adk")

_LAST_RUN: Dict[str, Any] = {"status": "NOT RUN", "detail": "No orchestrated request yet", "tools": [], "at": ""}


def configured() -> bool:
    return not CONFIG.demo_mode and CONFIG.adk_enabled and bool(CONFIG.project_id)


def importable() -> Tuple[bool, str]:
    try:
        import google.adk  # noqa: F401

        return True, getattr(google.adk, "__version__", "installed")
    except Exception as exc:  # pragma: no cover - depends on environment
        return False, str(exc)


def status() -> Tuple[str, str]:
    """ACTIVE / FALLBACK, reflecting the most recent real run when available."""
    if CONFIG.demo_mode:
        return "FALLBACK", "DEMO_MODE: ADK bypassed"
    if not CONFIG.adk_enabled:
        return "FALLBACK", "ADK_ENABLED=false → deterministic pipeline"
    ok, detail = importable()
    if not ok:
        return "FALLBACK", f"google-adk import failed: {detail[:120]}"
    if _LAST_RUN["status"] == "ACTIVE":
        return "ACTIVE", f"Orchestrator ran at {_LAST_RUN['at']} · tools: {', '.join(_LAST_RUN['tools']) or 'none'}"
    if _LAST_RUN["status"] == "FALLBACK":
        return "FALLBACK", f"Last turn fell back to the deterministic pipeline: {_LAST_RUN['detail']}"
    return "ACTIVE", f"google-adk {detail} loaded; orchestrator ready (no request yet)"


def _set_run(status_value: str, detail: str, tools: Optional[List[str]] = None) -> None:
    _LAST_RUN.update({"status": status_value, "detail": detail, "tools": tools or [], "at": utc_now()})


def _prepare_env() -> None:
    # ADK uses the Google Gen AI backend; route it to Vertex AI (ADC), not an API key.
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", CONFIG.project_id)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", CONFIG.vertex_location)


async def _run_turn(instruction: str, message: str, tools: List[Any], final_tools: Tuple[str, ...]) -> str:
    _prepare_env()
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    def end_after_final_tool(tool, args, tool_context, tool_response):
        # The turn's job is done once its final tool succeeds: skip the closing model call.
        if tool.name in final_tools and isinstance(tool_response, dict) and "error" not in tool_response:
            tool_context.actions.skip_summarization = True
        return None

    agent = LlmAgent(
        name="workpilot_orchestrator",
        model=CONFIG.gemini_model,
        instruction=instruction,
        tools=tools,
        after_tool_callback=end_after_final_tool,
    )
    # Each turn is short; durable workflow memory lives in Firestore and is read by a tool.
    app_name, user_id, session_id = "workpilot", "workpilot-user", "workpilot-turn"
    sessions = InMemorySessionService()
    await sessions.create_session(app_name=app_name, user_id=user_id, session_id=session_id)
    runner = Runner(agent=agent, app_name=app_name, session_service=sessions)
    content = types.Content(role="user", parts=[types.Part.from_text(text=message)])
    final_text = ""
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join((part.text or "") for part in event.content.parts)
    return final_text


def _run_sync(coro_factory):
    try:
        return asyncio.run(coro_factory())
    except RuntimeError as exc:
        if "asyncio.run()" in str(exc):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro_factory())
            finally:
                loop.close()
        raise


def run_turn(instruction: str, message: str, tools: List[Any], final_tools: Tuple[str, ...] = ()) -> str:
    """Run one agent turn with these tools and return its final text. Raises when ADK is unavailable or fails.

    The turn ends as soon as one of `final_tools` succeeds.
    """
    if not configured():
        raise RuntimeError("ADK not configured")
    return _run_sync(lambda: _run_turn(instruction, message, tools, tuple(final_tools)))


def record_run(ok: bool, detail: str, tools: List[str]) -> None:
    """What the last agent turn actually did, for System Status."""
    _set_run("ACTIVE" if ok else "FALLBACK", detail, list(dict.fromkeys(tools)))


def runtime_status() -> Dict[str, Any]:
    return dict(_LAST_RUN)
