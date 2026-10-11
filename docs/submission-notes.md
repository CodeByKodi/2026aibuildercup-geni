# Submission notes

- **Team:** GENI · **Challenge:** AI Builder Cup 2026, *Future of Work & Enterprise Productivity*.
- **All data is synthetic.** The six documents in `sample_data/` (status report, security
  assessment, performance test, UAT notes, issue tracker, architecture note) and the scanned
  CAB minutes describe a fictional release, *Customer Portal v12.4*, and fictional people.
  No customer or employee data is used.
- **Nothing is scripted.** Findings, contracts and outcome proposals come from the agent's
  turns on the evidence; outcomes are typed and confirmed by a person; time saved is a
  formula estimate with its assumptions shown in the app.
- **Fallbacks are labelled.** Every screen states which service produced its result
  (for example *Gemini on Vertex AI orchestrated by ADK* or `Local TF-IDF (FALLBACK)`).
- **Human control.** No agent tool can approve, execute or confirm; READY FOR APPROVAL is
  the best status the system can give.

## Known limitations

- One shared demo workspace: sessions are private to the browser that created them
  (My Sessions lists only those), but all sessions write to the same Firestore workspace.
- Gmail/Calendar execution uses one team demo mailbox; drafts are addressed to
  `<owner>@WORKPILOT_OWNER_EMAIL_DOMAIN` and are never sent.
- Uploads are limited to five documents per session and to the time before a decision exists.
- No sign-in: the prototype is public for judging, so approvals are recorded as the sample
  scenario's reviewer persona.

## Roadmap

Connected sources behind the same retrieval interface (`workpilot/retrieval.py`): Cloud
Storage, Google Drive, Jira, Confluence, SharePoint/Slack; per-user sign-in and approval
roles; owners recording their own outcomes from the action they receive.
