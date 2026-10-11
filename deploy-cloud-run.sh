#!/usr/bin/env bash
# Deploy WorkPilot AI (React UI + FastAPI + ADK agent) to Cloud Run from this repository.
# Safe to rerun: every step reuses what already exists.
set -euo pipefail

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
REGION="${2:-asia-south1}"
SERVICE="${3:-workpilot-ai}"
FIRESTORE_LOCATION="${4:-asia-south1}"
RUNTIME_SA_NAME="workpilot-runtime"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: ./deploy-cloud-run.sh <project-id> [region] [service-name] [firestore-location]"
  echo "Example: ./deploy-cloud-run.sh my-project asia-south1 workpilot-ai asia-south1"
  exit 1
fi

RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

command -v gcloud >/dev/null 2>&1 || {
  echo "ERROR: gcloud CLI is not installed."
  exit 1
}

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n1)"
if [[ -z "${ACTIVE_ACCOUNT}" ]]; then
  echo "ERROR: No active gcloud account. Run: gcloud auth login"
  exit 1
fi

echo "==> Active account: ${ACTIVE_ACCOUNT}"
echo "==> Project: ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" >/dev/null

# ADC quota-project mismatch is not fatal for Cloud Run deployment, but warn clearly.
ADC_QUOTA_PROJECT="$(python3 - <<'PY' 2>/dev/null || true
import json, os
p=os.path.expanduser('~/.config/gcloud/application_default_credentials.json')
try:
    with open(p) as f:
        print(json.load(f).get('quota_project_id',''))
except Exception:
    pass
PY
)"
if [[ -n "${ADC_QUOTA_PROJECT}" && "${ADC_QUOTA_PROJECT}" != "${PROJECT_ID}" ]]; then
  echo "WARNING: ADC quota project is '${ADC_QUOTA_PROJECT}', not '${PROJECT_ID}'."
  echo "         This does not block gcloud deployment, but local Google client libraries may warn."
  echo "         To fix later: gcloud auth application-default set-quota-project ${PROJECT_ID}"
fi

echo "==> Checking billing"
BILLING="$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)' 2>/dev/null || true)"
if [[ "${BILLING}" != "True" && "${BILLING}" != "true" ]]; then
  echo "ERROR: Billing is not enabled for project '${PROJECT_ID}'."
  echo "       Link an active billing account and rerun."
  exit 2
fi

echo "==> Enabling required Google Cloud APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  firebase.googleapis.com \
  documentai.googleapis.com \
  discoveryengine.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  serviceusage.googleapis.com \
  secretmanager.googleapis.com \
  gmail.googleapis.com \
  calendar-json.googleapis.com \
  --project "${PROJECT_ID}" --quiet

echo "==> Granting Cloud Run source-build permissions"
# `gcloud run deploy --source` builds with Cloud Build as the Compute Engine default service account.
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for ROLE in roles/run.builder roles/storage.objectViewer roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${BUILD_SA}" --role="${ROLE}" --quiet >/dev/null 2>&1 \
    || echo "WARNING: could not grant ${ROLE} to ${BUILD_SA}; grant it if the build fails with a permission error."
done

echo "==> Ensuring runtime service account exists"
if gcloud iam service-accounts describe "${RUNTIME_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Runtime service account already exists: ${RUNTIME_SA}"
else
  gcloud iam service-accounts create "${RUNTIME_SA_NAME}" \
    --display-name="WorkPilot AI runtime" \
    --project "${PROJECT_ID}" \
    --quiet
fi

# IAM can take a few seconds to see a newly-created service account.
echo "==> Waiting for service account propagation"
SA_READY=false
for _ in {1..18}; do
  if gcloud iam service-accounts describe "${RUNTIME_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    SA_READY=true
    break
  fi
  sleep 5
done

if [[ "${SA_READY}" != "true" ]]; then
  echo "ERROR: Runtime service account was created but is not yet visible to IAM."
  echo "       Wait 1 minute and rerun the script."
  exit 3
fi

echo "==> Granting runtime permissions"
RUNTIME_ROLES=(
  "roles/aiplatform.user"
  "roles/datastore.user"
  "roles/documentai.apiUser"
  "roles/documentai.viewer"  # processors.get, for the System Status processor check
  "roles/discoveryengine.editor"
  "roles/bigquery.dataEditor"
  "roles/bigquery.jobUser"
)

for ROLE in "${RUNTIME_ROLES[@]}"; do
  echo "    ${ROLE}"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="${ROLE}" \
    --quiet >/dev/null
 done

echo "==> Ensuring Firestore database exists"
# `describe` can occasionally return a non-zero status while a just-created
# database is still propagating. Listing databases is a more robust idempotency
# check, and the create path below also tolerates an ALREADY_EXISTS race.
DEFAULT_DB_NAME="projects/${PROJECT_ID}/databases/(default)"
EXISTING_DEFAULT_DB="$(
  gcloud firestore databases list \
    --project "${PROJECT_ID}" \
    --format='value(name)' 2>/dev/null | \
  grep -F "${DEFAULT_DB_NAME}" || true
)"

if [[ -n "${EXISTING_DEFAULT_DB}" ]]; then
  echo "Firestore database '(default)' already exists - continuing."
else
  echo "Creating Firestore database '(default)' in ${FIRESTORE_LOCATION}"
  FIRESTORE_CREATE_OUTPUT="$(mktemp)"
  set +e
  gcloud firestore databases create \
    --database='(default)' \
    --location="${FIRESTORE_LOCATION}" \
    --type=firestore-native \
    --project "${PROJECT_ID}" \
    --quiet >"${FIRESTORE_CREATE_OUTPUT}" 2>&1
  FIRESTORE_CREATE_RC=$?
  set -e

  if [[ ${FIRESTORE_CREATE_RC} -eq 0 ]]; then
    cat "${FIRESTORE_CREATE_OUTPUT}"
  elif grep -Eqi 'already exists|subject of a conflict|ALREADY_EXISTS' "${FIRESTORE_CREATE_OUTPUT}"; then
    echo "Firestore database '(default)' already exists - continuing."
  else
    cat "${FIRESTORE_CREATE_OUTPUT}" >&2
    rm -f "${FIRESTORE_CREATE_OUTPUT}"
    exit ${FIRESTORE_CREATE_RC}
  fi
  rm -f "${FIRESTORE_CREATE_OUTPUT}"
fi

# Optional richer services. The first Cloud Run deployment remains functional
# even before Document AI, Vertex AI Search, and BigQuery resources are provisioned.
# Document AI needs the processor id printed by scripts/bootstrap_cloud.py; without it,
# uploads use the local parsers and System Status says so.
DOC_ID="${DOCUMENT_AI_PROCESSOR_ID:-}"
DOC_ENABLED="${DOCUMENT_AI_ENABLED:-$([[ -n "${DOC_ID}" ]] && echo true || echo false)}"
DOC_LOC="${DOCUMENT_AI_LOCATION:-us}"
SEARCH_ENABLED="${VERTEX_SEARCH_ENABLED:-true}"
SEARCH_LOC="${VERTEX_SEARCH_LOCATION:-global}"
SEARCH_DS="${VERTEX_SEARCH_DATA_STORE_ID:-workpilot-knowledge}"
SEARCH_ENGINE="${VERTEX_SEARCH_ENGINE_ID:-workpilot-search}"
BQ_ENABLED="${BIGQUERY_ENABLED:-true}"
BQ_DATASET="${BIGQUERY_DATASET:-workpilot}"
BQ_TABLE="${BIGQUERY_TABLE:-events}"
WORKSPACE="${WORKPILOT_WORKSPACE:-builder-cup-demo}"
OWNER_DOMAIN="${WORKPILOT_OWNER_EMAIL_DOMAIN:-example.com}"
OWNER_EMAILS="${WORKPILOT_OWNER_EMAILS:-}"
IMPACT_WPM="${IMPACT_READING_WPM:-200}"
IMPACT_ACTION_MIN="${IMPACT_MINUTES_PER_ACTION:-5}"
IMPACT_FOLLOWUP_MIN="${IMPACT_MINUTES_PER_FOLLOWUP:-5}"
IMPACT_DECISION_MIN="${IMPACT_MINUTES_PER_DECISION:-10}"

# Gmail-draft / Calendar execution: mount the user OAuth token produced by
# scripts/authorize_workspace_oauth.py from Secret Manager. Without it the Approval
# step shows "Adapter not connected" and nothing executes.
OAUTH_SECRET_NAME="${OAUTH_SECRET_NAME:-workpilot-oauth-token}"
SECRET_FLAGS=()
if [[ -n "${OAUTH_SECRET_NAME}" ]]; then
  if gcloud secrets describe "${OAUTH_SECRET_NAME}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "==> Granting Secret Manager access for ${OAUTH_SECRET_NAME}"
    if ! gcloud secrets add-iam-policy-binding "${OAUTH_SECRET_NAME}" \
      --project "${PROJECT_ID}" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="roles/secretmanager.secretAccessor" \
      --quiet >/dev/null 2>&1; then
      # Some gcloud releases crash in this command (LayoutException); use the REST API instead.
      echo "    gcloud binding failed; granting through the Secret Manager API"
      SECRET_URL="https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${OAUTH_SECRET_NAME}"
      TOKEN="$(gcloud auth print-access-token)"
      POLICY="$(curl -sf -H "Authorization: Bearer ${TOKEN}" "${SECRET_URL}:getIamPolicy")"
      NEW_POLICY="$(python3 - "${POLICY}" "serviceAccount:${RUNTIME_SA}" <<'PY'
import json, sys
policy, member = json.loads(sys.argv[1] or "{}"), sys.argv[2]
role = "roles/secretmanager.secretAccessor"
binding = next((b for b in policy.setdefault("bindings", []) if b["role"] == role), None)
if binding is None:
    policy["bindings"].append({"role": role, "members": [member]})
elif member not in binding["members"]:
    binding["members"].append(member)
print(json.dumps({"policy": policy}))
PY
)"
      curl -sf -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
        -d "${NEW_POLICY}" "${SECRET_URL}:setIamPolicy" >/dev/null
    fi
    SECRET_FLAGS=(--set-secrets "WORKPILOT_OAUTH_TOKEN_JSON=${OAUTH_SECRET_NAME}:latest")
  else
    echo "WARNING: secret '${OAUTH_SECRET_NAME}' not found; Approval's Gmail/Calendar buttons stay disabled (not connected)."
  fi
fi

ENV_VARS="DEMO_MODE=false,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},FIREBASE_PROJECT_ID=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=TRUE,GEMINI_MODEL=gemini-2.5-flash,FIRESTORE_ENABLED=true,WORKPILOT_WORKSPACE=${WORKSPACE},ADK_ENABLED=true"
ENV_VARS+=",DOCUMENT_AI_ENABLED=${DOC_ENABLED},DOCUMENT_AI_LOCATION=${DOC_LOC},DOCUMENT_AI_PROCESSOR_ID=${DOC_ID}"
ENV_VARS+=",VERTEX_SEARCH_ENABLED=${SEARCH_ENABLED},VERTEX_SEARCH_LOCATION=${SEARCH_LOC},VERTEX_SEARCH_DATA_STORE_ID=${SEARCH_DS},VERTEX_SEARCH_ENGINE_ID=${SEARCH_ENGINE}"
ENV_VARS+=",BIGQUERY_ENABLED=${BQ_ENABLED},BIGQUERY_DATASET=${BQ_DATASET},BIGQUERY_TABLE=${BQ_TABLE}"
ENV_VARS+=",EXECUTION_ENABLED=true,WORKPILOT_OWNER_EMAIL_DOMAIN=${OWNER_DOMAIN},CLOUD_RUN_REGION=${REGION}"
ENV_VARS+=",IMPACT_READING_WPM=${IMPACT_WPM},IMPACT_MINUTES_PER_ACTION=${IMPACT_ACTION_MIN},IMPACT_MINUTES_PER_FOLLOWUP=${IMPACT_FOLLOWUP_MIN},IMPACT_MINUTES_PER_DECISION=${IMPACT_DECISION_MIN}"
if [[ -n "${OWNER_EMAILS}" ]]; then
  ENV_VARS+=",WORKPILOT_OWNER_EMAILS=${OWNER_EMAILS}"
fi

echo "==> Deploying WorkPilot AI to Cloud Run service ${SERVICE}"
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${RUNTIME_SA}" \
  --allow-unauthenticated \
  --set-env-vars "${ENV_VARS}" \
  ${SECRET_FLAGS[@]+"${SECRET_FLAGS[@]}"} \
  --memory 2Gi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 3 \
  --timeout 300 \
  --quiet

URL="$(gcloud run services describe "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format='value(status.url)')"

echo
echo "============================================================"
echo "WORKPILOT AI DEPLOYED"
echo "Prototype URL: ${URL}"
echo "============================================================"

HEALTH_PATH="/api/healthz"
if curl -fsS --max-time 30 "${URL}${HEALTH_PATH}" >/dev/null 2>&1; then
  echo "Health check: PASS"
else
  echo "Health check: WARNING"
  echo "The service deployed, but ${HEALTH_PATH} did not return 200 yet."
  echo "Check logs with:"
  echo "gcloud run services logs read ${SERVICE} --project ${PROJECT_ID} --region ${REGION} --limit 100"
fi

echo
echo "Submission URL: ${URL}"
