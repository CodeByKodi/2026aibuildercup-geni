# Google Cloud setup

WorkPilot runs on Gemini + Firestore + Cloud Run alone. Vertex AI Search, Document AI,
BigQuery and Gmail/Calendar are switched on with environment variables, without code changes;
until then each falls back with a visible label (see [architecture](architecture.md)).

## 0. Prerequisites

- A Google Cloud project with billing enabled, and `gcloud` signed in:
  `gcloud auth login` and `gcloud auth application-default login`.
- Python 3.11+ and Node 20+ for local runs.

## 1. Deploy the core service

```bash
export GOOGLE_CLOUD_PROJECT=<your-project-id>
./deploy-cloud-run.sh "$GOOGLE_CLOUD_PROJECT" asia-south1 workpilot-ai
```

The script is safe to rerun. It enables the APIs, grants Cloud Build's source-deploy
permissions, creates or reuses the `workpilot-runtime` service account and its roles
(Vertex AI user, Datastore user, Document AI user + viewer, Discovery Engine editor,
BigQuery data editor + job user), ensures the Firestore database exists, deploys from this
directory with `gcloud run deploy --source .`, allows unauthenticated access, prints the
URL and checks `/api/healthz`.

## 2. Provision Vertex AI Search, Document AI and BigQuery

```bash
pip install -r requirements-dev.txt
python scripts/bootstrap_cloud.py
```

It creates (idempotently) a Document AI OCR processor, an Agent Search data store and
engine, and the BigQuery `workpilot.events` table, then prints the variables to export:

```bash
export DOCUMENT_AI_PROCESSOR_ID=<printed processor id>
export VERTEX_SEARCH_DATA_STORE_ID=workpilot-knowledge
export VERTEX_SEARCH_ENGINE_ID=workpilot-search
./deploy-cloud-run.sh "$GOOGLE_CLOUD_PROJECT" asia-south1 workpilot-ai
```

The service indexes the six sample documents into the data store on first start; indexing
can take a few minutes, during which Knowledge shows the labelled local fallback.

## 3. Optional: real Gmail drafts and Calendar events

A service account cannot write into a person's mailbox, so a user consents once:

1. Console → APIs & Services → OAuth consent screen (External; add the demo account as a
   test user) → Credentials → OAuth client ID → *Desktop app*; download the JSON and keep
   it outside the repository.
2. `python scripts/authorize_workspace_oauth.py --client-secrets <downloaded.json> --project "$GOOGLE_CLOUD_PROJECT"`
   stores the refresh token in Secret Manager as `workpilot-oauth-token`.
3. Redeploy: the script grants the runtime account access and mounts the secret.

Drafts are created without being sent; the run buttons on Approval stay disabled until
this is configured.

## 4. Check what is live

Open **System Status** in the app (or `GET /api/status`). Each service is ACTIVE only after
a live check; Vertex AI Agent Engine and AlloyDB are listed as NOT USED.

## Design choices

- **Cloud Run, not Agent Engine:** the ADK agent runs inside the same service as the API;
  its memory is Firestore, so a separate agent runtime adds nothing here.
- **Firestore, not AlloyDB:** serverless documents fit sessions, contracts and the audit
  trail without operating a database.
