# Three-minute demo flow

Fresh session, nothing to set up. Times are typical on the live service.

| # | Step | Click | What to point out |
|---|---|---|---|
| 1 | **Start** | *Go-live readiness*, then **Analyze with WorkPilot** | The question: *"Are we ready to go live? What is blocking the release, what should we prioritize this week, who owns each action, and what must be completed before approval?"* Optionally add your own document first; a scanned PDF is read by Document AI. |
| 2 | **Knowledge** (~4 s) | **Next: Analyze** | Vertex AI Search returned cited passages; each source card shows its real type, size and citation count. |
| 3 | **Analyze** (~20–30 s) | **Next: Decision** | Live checklist with server-measured stage times. Initial assessment **NOT READY**: three blockers, three owners, three approval conditions. Labelled *Gemini on Vertex AI orchestrated by ADK*. |
| 4 | **Decision** | **Prepare Action Contracts** (~8 s) | Decision Record: status, id, rationale, key evidence (source → blocker), numbered approval conditions. |
| 5 | **Actions** | **Next: Approval** | One owned, dated contract per blocker with expected outcome and evidence. Nothing has run; the agent is paused. |
| 6 | **Approval** | **Approve all pending**, then **Run approved as Gmail drafts** | Approval is a person's act and is timestamped. Each run creates a real Gmail draft (never sent); the receipt is Gmail's draft id. |
| 7 | **Outcome & Impact** | Type one line per action → **Ask the agent for each** → choose the status → **Confirm** | The screen says what is waiting for you. Confirming the last outcome resumes the agent from Firestore by itself. Before **NOT READY** → now **READY FOR APPROVAL**; 3 of 3 conditions met; receipts, expected vs actual outcome with who confirmed it; estimated time saved with its formula; the Google Cloud proof and the audit trail with event ids. |

Then show **My Sessions** (the session with its stage and readiness), **Decision Memory**
(expected vs actual outcome) and **System Status** (each Google service and whether it is
live). Reload the page at any point: the workflow comes back from Firestore.

The **Impact & Proof** strip above each step (docs searched, cited passages, measured analysis time, blockers, owners, conditions, manual baseline vs WorkPilot run, actions executed without approval) and the clickable source chips answer most "how do you know?" questions in a few seconds.

Honesty points worth saying out loud:

- Outcomes are written and confirmed by a person; the agent only proposes.
- *Partially achieved* keeps a condition open.
- READY FOR APPROVAL is the best status: go-live approval itself stays human.
- Time saved is a formula estimate, shown with its assumptions, not measured time.
