"""Truthful Google Cloud stack status.

Each row is produced by a verification call (cached per process) rather than
by reading a feature flag, so the UI never claims ACTIVE for something that
has not actually responded.
"""
import os
from typing import Dict, List

from . import adk_orchestrator, ai, analytics, document_ai, enterprise_search, execution
from .config import CONFIG
from .state_store import backend_status, get_db


def _cloud_run_status():
    service = os.getenv("K_SERVICE")
    if service:
        return "ACTIVE", f"service {service} · revision {os.getenv('K_REVISION', '?')} · region {os.getenv('CLOUD_RUN_REGION', 'asia-south1')}"
    return "LOCAL", "Not running on Cloud Run in this process"


def _firestore_status():
    label, detail = backend_status()
    mapping = {"Connected": "ACTIVE", "Demo": "FALLBACK", "Disabled": "NOT CONFIGURED", "Fallback": "ERROR"}
    return mapping.get(label, label.upper()), detail


def _firebase_status():
    if CONFIG.demo_mode:
        return "FALLBACK", "DEMO_MODE: Firebase Admin SDK not initialised"
    try:
        import firebase_admin

        get_db()
        if firebase_admin._apps:
            return "ACTIVE", f"firebase-admin initialised for project {CONFIG.project_id}"
        return "ERROR", "Firebase Admin SDK did not initialise"
    except Exception as exc:  # pragma: no cover
        return "ERROR", f"{type(exc).__name__}: {str(exc)[:120]}"


def technology_status() -> List[Dict[str, str]]:
    gemini = ai.status()
    search = enterprise_search.status()
    docai = document_ai.status()
    adk = adk_orchestrator.status()
    bq = analytics.status()
    gmail = execution.gmail_status()
    calendar = execution.calendar_status()
    firebase = _firebase_status()
    firestore = _firestore_status()
    cloud_run = _cloud_run_status()
    return [
        {"technology": "Gemini on Vertex AI", "status": gemini[0], "detail": gemini[1], "use": "Reasoning, blocker analysis, Action Contracts, outcome assessment"},
        {"technology": "Cloud Run", "status": cloud_run[0], "detail": cloud_run[1], "use": "Public application hosting"},
        {"technology": "Firestore", "status": firestore[0], "detail": firestore[1], "use": "Decisions, actions, executions, audit events, impact"},
        {"technology": "Firebase Admin SDK", "status": firebase[0], "detail": firebase[1], "use": "Backend Firestore access with ADC"},
        {"technology": "Vertex AI Search / Agent Search", "status": search[0], "detail": search[1], "use": "Grounded enterprise retrieval"},
        {"technology": "Document AI", "status": docai[0], "detail": docai[1], "use": "PDF / scanned PDF / image OCR"},
        {"technology": "ADK", "status": adk[0], "detail": adk[1], "use": "WorkPilot Orchestrator agent + Knowledge/Decision/Action/Outcome tools, on Cloud Run"},
        {"technology": "BigQuery", "status": bq[0], "detail": bq[1], "use": "Event analytics and impact measurement"},
        {"technology": "Gmail (drafts only)", "status": gmail[0], "detail": gmail[1], "use": "Act — Gmail draft for the owner after approval"},
        {"technology": "Google Calendar", "status": calendar[0], "detail": calendar[1], "use": "Act — Calendar event on the due date after approval"},
        {"technology": "Vertex AI Agent Engine", "status": "NOT USED", "detail": "Cloud Run is the runtime; the ADK agent runs inside the service", "use": "—"},
        {"technology": "AlloyDB", "status": "NOT USED", "detail": "Firestore chosen deliberately", "use": "—"},
    ]
