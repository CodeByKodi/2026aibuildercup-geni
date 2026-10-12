# WorkPilot AI · 3-minute demo video

**Watch:** [workpilot-demo.mp4](workpilot-demo.mp4) (2:32, 1920×1080, Indian English narration with captions).

Knowledge → Decision → **Approval** → Action → Outcome → Impact, recorded from the live Cloud Run deployment.

`record-demo.mjs` drives the real app with Playwright. Nothing is mocked. Chrome's screencast captures the
frames, and ffmpeg builds a 1920×1080, 30 fps MP4 with captions and a narration track.

- **Waits are sped up honestly.** Long AI steps play faster than real time, and a badge in the top right counts
  the real seconds while they play. The badge ends on, for example, `✓ Gemini via ADK · real time 14 s`.
- **Spoken numbers come from the screen.** Every number in the narration is read during the run: documents,
  blockers, conditions, contracts, readiness, baseline and run time. If the app shows something different,
  the narration says it.
- **A person decides each outcome.** The outcome lines typed for Maya restate each contract's own expected
  outcome and start with "Demo run:". The agent proposes a status and the script keeps it. Only ACHIEVED
  closes a condition.

## Record

```bash
npm ci && npx playwright install chromium
node demo/record-demo.mjs --out ./demo-out
```

Options: `--url <deployment>` (default: the final panel URL), `--no-voice` (captions only), `--voice Aman` (Indian English, male; any macOS voice works),
`--rate 170`, `--tone soft` (default: slightly deeper, softer highs, faint room; `plain` for the raw voice).

Needs ffmpeg. The synthetic voice needs macOS `say`.

Each run creates one session and three Gmail drafts in the deployment's demo mailbox. The drafts are never sent.

Output in `--out`:

| File | What |
|---|---|
| `workpilot-demo.mp4` | Final video: captions and synthetic narration |
| `workpilot-demo-captions.mp4` | Same video, silent: for recording your own voice-over |
| `timeline.md` | Step and narration cue times for this take, and the facts read from the screen |

## Screen sequence and timing (published take, 12 Oct 2026, 2:32, session `SES-f14595b573a6`)

| At | Step | On screen | Wait (real → in video) |
|---|---|---|---|
| 0:00 | Title | Title card: WorkPilot AI, team GENI, the flow, the Google Cloud stack | none |
| 0:09 | 1 · Ask | Go-live scenario chip; the question is typed; **Analyze with WorkPilot** | none |
| 0:18 | 2 · Knowledge | Six source cards with real file metadata and what Vertex AI Search cited | ~3 s → 2 s |
| 0:26 | 3 · Analyze | Analysis progress, then **NOT READY FOR GO-LIVE**, three blocker cards; a citation opens the evidence drawer | ~15 s → 5 s |
| 0:49 | 4 · Decision | Decision Record: recommendation, rationale, three approval conditions | none |
| 0:59 | 5 · Action Contracts | **Prepare Action Contracts**; first contract with its precondition; Gmail preview ("nothing sent") | ~15 s → 4 s |
| 1:17 | 6 · Human approval | **Approve all pending**; **Run 3 approved as Gmail drafts**; receipts with Gmail ids | ~5 s → 3 s |
| 1:34 | 7 · Outcome | "Waiting for you"; Maya types what happened; **Ask the agent for each**; status ACHIEVED; **Confirm** | ~40 s → 4 s, ~15 s → 4 s |
| 2:01 | 8 · Ready for approval | Before NOT READY → now READY FOR APPROVAL; 3 of 3 conditions met; agent message from Firestore | none |
| 2:11 | 9 · Impact | WorkPilot Impact 1.8 hrs; 108 min baseline − measured run; breakdown table | none |
| 2:24 | Close | End card: the flow, live URL, repository | none |

The waits are what the badge showed in this take. They vary a little from run to run, but the in-video
length of each sped-up wait is fixed, so the total stays about the same.

## Narration (published take)

> This is WorkPilot AI, from team GENI. It takes a team from scattered documents to an approved, executed and
> measured decision, live on Google Cloud.
>
> Maya Chen, the release manager for Customer Portal version twelve, asks one question.
>
> Vertex AI Search looked through the team's 6 release documents in 3 seconds. Each card shows the real file,
> and what the search cited from it.
>
> Gemini, orchestrated by the Agent Development Kit, read the evidence in 14 seconds. The verdict: not ready
> for go-live. 3 blockers, each with an owner, a due date and its sources. Click a source, and you see the
> exact passage behind the claim.
>
> The decision is saved in Firestore as a Decision Record. A recommendation, its rationale, and 3 approval
> conditions that must close before go-live.
>
> The agent turns each blocker into an Action Contract: 3 of them, with owner, due date and expected outcome.
> Each runs only after a person approves. This is the exact Gmail follow-up it would create. Nothing is sent.
>
> Now the human gate. The agent cannot approve or run anything itself. Maya approved all 3. One click runs
> them as real Gmail drafts. Each receipt is the ID that Gmail returned.
>
> On Outcome, Maya records what actually happened for each action. The agent reads each outcome against its
> contract and proposes achieved for all of them. But the person decides and confirms. Only an outcome
> confirmed as achieved closes a condition.
>
> With the last outcome confirmed, the agent resumed from its saved state and reassessed readiness. Not ready
> is now ready for approval. The final go-live decision stays with a person.
>
> The impact: about 1.8 hours saved on this one decision. That is a 108 minute manual baseline, minus
> WorkPilot's measured 60 second run, with every assumption shown.
>
> WorkPilot AI. From knowledge to outcomes, with people in control.

That is about 330 words. To use your own voice, record it over `workpilot-demo-captions.mp4`, using the cue
times in `timeline.md`.
