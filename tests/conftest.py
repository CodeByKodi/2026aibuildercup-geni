import os
import sys
from pathlib import Path

# Tests never touch Google Cloud: force demo mode before workpilot is imported.
os.environ["DEMO_MODE"] = "true"
os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
os.environ["WORKPILOT_WORKSPACE"] = "test-workspace"
os.environ.setdefault("IMPACT_READING_WPM", "200")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from workpilot import state_store  # noqa: E402
from workpilot.config import CONFIG  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_memory_store():
    state_store.reset_memory()
    yield
    state_store.reset_memory()


@pytest.fixture
def set_config():
    """Temporarily override frozen CONFIG fields."""
    originals = {}

    def _set(**kwargs):
        for key, value in kwargs.items():
            originals.setdefault(key, getattr(CONFIG, key))
            object.__setattr__(CONFIG, key, value)

    yield _set
    for key, value in originals.items():
        object.__setattr__(CONFIG, key, value)


@pytest.fixture
def sample_docs():
    from workpilot.documents import load_sample_documents

    return dict(load_sample_documents(str(ROOT / "sample_data")))


class _LiveAdapter:
    """Stands in for GoogleWorkspaceAdapter: returns IDs the way the APIs do."""

    mode = "REAL"

    def __init__(self):
        self.calls = []

    def create_gmail_draft(self, to, subject, body):
        from workpilot.execution import ExecutionResult

        self.calls.append(("gmail", to, subject, body))
        return ExecutionResult("gmail:draft/r-123", to, "Gmail draft r-123 created; not sent.")

    def create_calendar_followup(self, title, description, when, attendee):
        from workpilot.execution import ExecutionResult

        self.calls.append(("calendar", attendee, title, description))
        return ExecutionResult("https://calendar.google.com/event?eid=ev9", f"primary calendar · {when.isoformat()}", "event")


@pytest.fixture
def live_adapter(monkeypatch):
    """A connected Gmail/Calendar adapter without network calls."""
    from workpilot import execution

    adapter = _LiveAdapter()
    monkeypatch.setattr(execution, "get_adapter", lambda: adapter)
    return adapter
