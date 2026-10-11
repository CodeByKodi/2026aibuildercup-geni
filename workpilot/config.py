import os
from dataclasses import dataclass


def truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class CloudConfig:
    project_id: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    vertex_location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    demo_mode: bool = truthy("DEMO_MODE", "true")

    firestore_enabled: bool = truthy("FIRESTORE_ENABLED", "true")
    workspace: str = os.getenv("WORKPILOT_WORKSPACE", "builder-cup-demo")

    document_ai_enabled: bool = truthy("DOCUMENT_AI_ENABLED", "false")
    document_ai_location: str = os.getenv("DOCUMENT_AI_LOCATION", "us")
    document_ai_processor_id: str = os.getenv("DOCUMENT_AI_PROCESSOR_ID", "")

    vertex_search_enabled: bool = truthy("VERTEX_SEARCH_ENABLED", "false")
    vertex_search_location: str = os.getenv("VERTEX_SEARCH_LOCATION", "global")
    vertex_search_engine_id: str = os.getenv("VERTEX_SEARCH_ENGINE_ID", "")
    vertex_search_data_store_id: str = os.getenv("VERTEX_SEARCH_DATA_STORE_ID", "")

    adk_enabled: bool = truthy("ADK_ENABLED", "true")

    bigquery_enabled: bool = truthy("BIGQUERY_ENABLED", "false")
    bigquery_dataset: str = os.getenv("BIGQUERY_DATASET", "workpilot")
    bigquery_table: str = os.getenv("BIGQUERY_TABLE", "events")
    bigquery_location: str = os.getenv("BIGQUERY_LOCATION", "US")


    # Follow-up execution (Gmail drafts / Calendar events).
    # REAL execution needs a user OAuth refresh token because the Cloud Run
    # service account cannot act inside a consumer Gmail mailbox. The token
    # JSON is mounted from Secret Manager into WORKPILOT_OAUTH_TOKEN_JSON.
    execution_enabled: bool = truthy("EXECUTION_ENABLED", "true")
    oauth_token_json: str = os.getenv("WORKPILOT_OAUTH_TOKEN_JSON", "")
    oauth_token_file: str = os.getenv("WORKPILOT_OAUTH_TOKEN_FILE", "")
    default_owner_email_domain: str = os.getenv("WORKPILOT_OWNER_EMAIL_DOMAIN", "example.com")
    owner_email_map: str = os.getenv("WORKPILOT_OWNER_EMAILS", "")  # "Mei=mei@x.com,Arjun=arjun@x.com"


@dataclass(frozen=True)
class ImpactAssumptions:
    """Transparent, configurable assumptions used for WorkPilot Impact.

    Every number on the Impact screen is derived from recorded events and
    these constants. They are displayed in the UI under "Impact methodology".
    """

    reading_words_per_minute: float = _float("IMPACT_READING_WPM", 200.0)
    minutes_per_prepared_action: float = _float("IMPACT_MINUTES_PER_ACTION", 5.0)
    minutes_per_followup: float = _float("IMPACT_MINUTES_PER_FOLLOWUP", 5.0)
    minutes_per_decision: float = _float("IMPACT_MINUTES_PER_DECISION", 15.0)
    minutes_per_blocker: float = _float("IMPACT_MINUTES_PER_BLOCKER", 10.0)
    minutes_per_owner_coordination: float = _float("IMPACT_MINUTES_PER_OWNER", 15.0)
    # "Weekly" figures use events from the trailing window below.
    window_days: int = int(_float("IMPACT_WINDOW_DAYS", 7))


CONFIG = CloudConfig()
IMPACT = ImpactAssumptions()
