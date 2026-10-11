"""One-time OAuth consent for REAL Gmail-draft / Calendar execution.

Why: the Cloud Run service account cannot create drafts inside a consumer
Gmail mailbox. A user must consent once; the resulting refresh token is stored
in Secret Manager and mounted into Cloud Run as WORKPILOT_OAUTH_TOKEN_JSON.

Prerequisites (Google Cloud console, once):
  1. APIs & Services → OAuth consent screen → External, publish or add the
     demo account as a test user.
  2. APIs & Services → Credentials → Create credentials → OAuth client ID →
     Desktop app. Download the JSON.

Usage:
  python scripts/authorize_workspace_oauth.py --client-secrets ~/Downloads/client_secret_xxx.json \
      --project <your-project-id> --secret workpilot-oauth-token

The script prints the token JSON path and creates/updates the Secret Manager
secret. Nothing is committed to the repository; keep the downloaded client
JSON outside the repo.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-secrets", required=True, help="Downloaded OAuth Desktop client JSON")
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    parser.add_argument("--secret", default="workpilot-oauth-token")
    parser.add_argument("--no-secret", action="store_true", help="Only write the token file; skip Secret Manager")
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "workpilot_oauth_token.json"))
    args = parser.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        print("ERROR: no refresh token returned. Re-run and ensure you grant offline access.", file=sys.stderr)
        return 1

    token = json.loads(creds.to_json())
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(token, handle)
    os.chmod(args.out, 0o600)
    print(f"Token written to {args.out} (keep this out of git)")

    if args.no_secret:
        return 0
    if not args.project:
        print("ERROR: --project is required to store the secret", file=sys.stderr)
        return 1

    describe = subprocess.run(
        ["gcloud", "secrets", "describe", args.secret, "--project", args.project],
        capture_output=True,
        text=True,
    )
    if describe.returncode != 0:
        subprocess.run(
            ["gcloud", "secrets", "create", args.secret, "--replication-policy=automatic", "--project", args.project, "--quiet"],
            check=True,
        )
    subprocess.run(
        ["gcloud", "secrets", "versions", "add", args.secret, "--data-file", args.out, "--project", args.project, "--quiet"],
        check=True,
    )
    print(f"Secret Manager: {args.secret} updated in {args.project}")
    print("\nNext: grant the runtime service account access and redeploy with the secret mounted:")
    print(f"  gcloud secrets add-iam-policy-binding {args.secret} --project {args.project} \\")
    print(f"    --member serviceAccount:workpilot-runtime@{args.project}.iam.gserviceaccount.com --role roles/secretmanager.secretAccessor")
    print(f"  OAUTH_SECRET_NAME={args.secret} ./deploy-cloud-run.sh {args.project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
