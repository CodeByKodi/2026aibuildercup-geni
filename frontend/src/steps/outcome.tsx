import { useEffect, useState } from 'react'
import { StepNav, type StepProps } from '../App'
import { api, type Audit, type Contract, type Impact, type Session, type Suggestion } from '../api'
import { Avatar, Badge, Button, Empty, Icon, fmtDateTime, seconds } from '../ui'
import { GcpProofCard } from '../proof'

/** "3 of 3 approval conditions open", without repeating the status beside it. */
export function openLine(v: { status: string; headline: string } | null | undefined): string {
  if (!v) return ''
  return v.headline.startsWith(`${v.status} — `) ? v.headline.slice(v.status.length + 3) : v.headline
}

interface Draft { actual: string; status: string; suggestion?: Suggestion; asking?: boolean; error?: string }

/** Audit events for this session, re-read whenever recorded work changes. */
function useAudit(s: Session | null) {
  const [audit, setAudit] = useState<Audit | null>(null)
  const sid = s?.session_id || ''
  const p = s?.progress
  useEffect(() => {
    if (!sid || !p?.total) return
    api.audit(sid).then(setAudit).catch(() => setAudit(null))
  }, [sid, p?.total, p?.approved, p?.rejected, p?.executed, p?.measured, s?.agent_message?.at])
  return audit
}

// --------------------------------------------------------------------------
// 7 · Outcome & Impact
// --------------------------------------------------------------------------
export function OutcomeStep({ s, cfg, go, run, busy }: StepProps) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const audit = useAudit(s)
  if (!s?.contracts.length || !s.analysis) return <Empty title="No outcomes yet">Approve and run Action Contracts first.</Empty>
  const ready = s.readiness?.status === 'READY FOR APPROVAL'
  const before = s.analysis.initial_verdict
  const now = s.analysis.verdict
  const live = s.contracts.filter((c) => c.approval !== 'Rejected')
  const open = live.filter((c) => c.outcome_status === 'PENDING' && c.execution === 'Executed')
  const msg = s.agent_message
  const waitingRun = s.contracts.filter((c) => c.approval === 'Approved' && c.execution !== 'Executed').length
  const allRecorded = live.length > 0 && live.every((c) => c.outcome_status !== 'PENDING')
  // After every outcome is recorded the agent resumes on its own; a re-check is offered only when its view is out of date.
  const canRecheck = allRecorded && s.agent.can_resume && !(msg?.ok && s.agent_message_current)
  /** When the last outcome is confirmed, the agent resumes from Firestore by itself. */
  const autoResume = async (out: Session) => (out.stage === 'IMPACT' && out.agent.can_resume ? api.resume(out.session_id) : out)
  const draft = (id: string): Draft => drafts[id] ?? { actual: '', status: '' }
  const patch = (id: string, d: Partial<Draft>) => setDrafts((all) => ({ ...all, [id]: { ...(all[id] ?? { actual: '', status: '' }), ...d } }))
  const confirmedBy = (id: string) => audit?.events.find((e) => e.action_id === id && e.event_type === 'outcome_verified')

  const ask = async (c: Contract) => {
    const d = draft(c.action_id)
    if (!d.actual.trim()) return
    patch(c.action_id, { asking: true, error: '' })
    try {
      const out = await api.suggest(s.session_id, c.action_id, d.actual)
      setDrafts((all) => {
        const cur = all[c.action_id] ?? { actual: '', status: '' }
        const status = cur.status || (cfg.outcome_statuses.includes(out.suggested_status) && out.suggested_status !== 'PENDING' ? out.suggested_status : '')
        return { ...all, [c.action_id]: { ...cur, asking: false, suggestion: out, status } }
      })
    } catch (e) {
      patch(c.action_id, { asking: false, error: (e as Error).message })
    }
  }
  const toConfirm = open.filter((c) => draft(c.action_id).actual.trim() && draft(c.action_id).status)
  const confirmAll = () => run('confirm-all', async () => {
    let out: Session | undefined
    for (const c of toConfirm) {
      const d = draft(c.action_id)
      out = await api.confirm(s.session_id, c.action_id, d.actual.trim(), d.status)
    }
    return out ? autoResume(out) : out
  })

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>{ready ? 'All Conditions Satisfied' : 'Outcomes and Readiness'}</h2>
          <p className="sub">{ready ? 'The confirmed outcomes have resolved every approval condition.' : 'A person records what actually happened; readiness is recomputed from those outcomes.'}</p>
        </div>
      </div>

      <div className="before-after" data-testid="before-after">
        <div className={`ba tone-${before?.tone ?? 'red'}`}>
          <span className="label">Before · at the decision</span><b>{before?.status}</b><span className="meta">{openLine(before)}</span>
        </div>
        <span className="ba-arrow"><Icon name="arrow" size={22} /></span>
        <div className={`ba tone-${s.readiness?.tone ?? now?.tone ?? 'red'}`} data-testid="readiness">
          <span className="label">Now · from confirmed outcomes</span>
          <b data-testid="readiness-status">{s.readiness?.status ?? now?.status}</b>
          <span className="meta">{ready ? 'All approval conditions are satisfied. Final production approval remains a human decision.' : s.readiness?.text ?? openLine(now)}</span>
        </div>
      </div>

      <div className="card">
        <h3 data-testid="conditions-met">Approval conditions · {now ? now.total - now.open : 0} of {now?.total ?? 0} met</h3>
        <ol className="conditions" data-testid="conditions">
          {s.analysis.sections.approval_conditions.map((c) => <li key={c}>{c}</li>)}
        </ol>
        <p className="meta">A condition closes only through an outcome a person confirmed as ACHIEVED; partially achieved keeps it open.</p>
      </div>

      {waitingRun > 0 ? (
        <div className="next-step" data-testid="next-step" data-next="run">
          <div>
            <b><Icon name="pause" size={16} /> Waiting for you: {waitingRun} approved action{waitingRun === 1 ? " hasn't" : "s haven't"} run yet</b>
            <span className="meta">The agent stays paused until the approved work runs and a person confirms what happened. Each run creates a real Gmail draft in the connected mailbox; nothing is sent.</span>
          </div>
          <Button icon="mail" busy={busy === 'run-all'} disabled={!!busy || !s.followups_connected} testid="run-all-outcome"
            title={s.followups_connected ? undefined : 'Gmail is not connected on this deployment'}
            onClick={() => run('run-all', () => api.executeApproved(s.session_id, 'gmail_draft'))}>Run {waitingRun} approved as Gmail drafts</Button>
        </div>
      ) : open.length > 0 ? (
        <div className="next-step" data-testid="next-step" data-next="confirm">
          <div>
            <b><Icon name="pause" size={16} /> Waiting for you: confirm {open.length} outcome{open.length === 1 ? '' : 's'} below</b>
            <span className="meta">Write what actually happened, ask the agent for its view if you like, choose the status and confirm. When the last one is confirmed, the agent resumes from Firestore and reassesses readiness.</span>
          </div>
        </div>
      ) : null}
      {busy === 'confirm-all' || busy.startsWith('confirm:') ? <p className="meta"><span className="spinner dark" /> Recording the outcome{open.length > 1 ? 's' : ''}; the agent resumes when the last one is in…</p> : null}
      {msg ? (
        <div className={`agent-msg${msg.ok ? (s.agent_message_current ? '' : ' stale') : ' failed'}`} data-testid="agent-message" data-ok={msg.ok} data-fallback={!!msg.fallback}>
          <b>{msg.fallback ? 'Readiness rechecked without the agent' : msg.ok ? 'Agent resumed from Firestore' : 'Agent could not resume'} · {fmtDateTime(msg.at)}</b>
          <p>{msg.text}</p>
          {msg.steps?.length ? <p className="meta">{msg.fallback ? 'Steps' : 'Tools'}: {msg.steps.join(' → ')}</p> : null}
        </div>
      ) : null}
      {canRecheck ? (
        <div className="center-row left">
          <Button kind="secondary" icon="play" busy={busy === 'resume'} onClick={() => run('resume', () => api.resume(s.session_id))} testid="resume-agent">
            {msg ? 'Re-check readiness with the agent' : 'Resume the agent'}
          </Button>
          <span className="meta">{msg ? 'People have acted since the agent last looked.' : 'The agent re-reads the saved workflow and reassesses readiness and impact. It cannot approve or confirm anything.'}</span>
        </div>
      ) : null}

      <div className="outcome-grid">
        <div className="card">
          <div className="card-head">
            <h3>Action Outcomes</h3>
            {open.length ? (
              <div className="bulk-row tight">
                <Button kind="secondary" icon="spark" testid="ask-agent-all"
                  disabled={!open.some((c) => draft(c.action_id).actual.trim()) || open.some((c) => draft(c.action_id).asking)}
                  onClick={() => open.forEach((c) => { if (draft(c.action_id).actual.trim() && !draft(c.action_id).suggestion) ask(c) })}>
                  Ask the agent for each
                </Button>
                <Button icon="check" testid="confirm-all" busy={busy === 'confirm-all'} disabled={!toConfirm.length || !!busy} onClick={confirmAll}>
                  Confirm {toConfirm.length || ''} outcome{toConfirm.length === 1 ? '' : 's'}
                </Button>
              </div>
            ) : null}
          </div>
          <ul className="outcomes" data-testid="outcomes">
            {live.map((c) => {
              const recorded = c.outcome_status !== 'PENDING'
              const d = draft(c.action_id)
              const by = confirmedBy(c.action_id)
              return (
                <li key={c.action_id} className={recorded ? 'recorded' : ''} data-testid="outcome-row" data-outcome={c.outcome_status}>
                  <div className="outcome-head">
                    <span className={`tick ${recorded ? (c.outcome_status === 'ACHIEVED' ? 'done' : 'warn') : ''}`}>{recorded ? <Icon name="check" size={14} /> : null}</span>
                    <b>{c.title}</b>
                    <Avatar name={c.owner} initials={c.initials} />
                    {recorded ? <Badge>{c.outcome_status.replace(/_/g, ' ')}</Badge> : <Badge>{c.execution}</Badge>}
                  </div>
                  {c.receipt ? (
                    <p className="receipt-line" data-testid="receipt-line">
                      <Icon name={c.receipt.kind.startsWith('Gmail') ? 'mail' : 'calendar'} size={14} /> {c.receipt.kind} <code>{c.receipt.reference}</code> · action <code>{c.action_id}</code> · owner {c.owner} · {fmtDateTime(c.receipt.at)}
                    </p>
                  ) : null}
                  {recorded ? (
                    <div className="evidence-line" data-testid="outcome-evidence">
                      <p className="actual"><span className="label">Expected</span>{c.expected_outcome || 'not stated'}</p>
                      <p className="actual"><span className="label">Actual · confirmed by a person</span>{c.actual_outcome}</p>
                      {by ? <p className="meta">Confirmed by {by.by.replace('Human reviewer as ', '') || 'a reviewer'} · {fmtDateTime(by.at)} · event <code>{by.event_id.slice(0, 12)}</code></p> : null}
                    </div>
                  ) : c.execution === 'Executed' ? (
                    <div className="outcome-form" data-testid="outcome-form">
                      <label htmlFor={`actual-${c.action_id}`}>What actually happened? <span className="meta">Expected: {c.expected_outcome || 'not stated'}</span></label>
                      <textarea id={`actual-${c.action_id}`} rows={2} value={d.actual} onChange={(e) => patch(c.action_id, { actual: e.target.value, suggestion: undefined })}
                        placeholder="What was verified, by whom, and where the evidence is" data-testid="actual-outcome" />
                      <div className="outcome-actions">
                        <Button kind="secondary" icon="spark" onClick={() => ask(c)} busy={d.asking} disabled={!d.actual.trim()} testid="ask-agent" title="The agent proposes a status. You confirm.">Ask the agent</Button>
                        <select value={d.status} onChange={(e) => patch(c.action_id, { status: e.target.value })} aria-label="Outcome status" data-testid="outcome-status">
                          <option value="">Outcome status…</option>
                          {cfg.outcome_statuses.filter((x) => x !== 'PENDING').map((x) => <option key={x} value={x}>{x.replace(/_/g, ' ')}</option>)}
                        </select>
                        <Button onClick={() => run(`confirm:${c.action_id}`, async () => autoResume(await api.confirm(s.session_id, c.action_id, d.actual.trim(), d.status)))}
                          busy={busy === `confirm:${c.action_id}`} disabled={!d.actual.trim() || !d.status || !!busy} testid="confirm-outcome">Confirm</Button>
                      </div>
                      {d.suggestion ? (
                        <p className="suggestion" data-testid="suggestion">
                          Agent proposes <Badge>{d.suggestion.suggested_status.replace(/_/g, ' ')}</Badge> — {d.suggestion.explanation} <span className="meta">({d.suggestion.mode}). You confirm.</span>
                        </p>
                      ) : null}
                      {d.error ? <p className="error">{d.error}</p> : null}
                    </div>
                  ) : <p className="meta">Run this approved action on Approval before recording its outcome. <button className="link-btn" onClick={() => go('approval')}>Go to Approval</button></p>}
                </li>
              )
            })}
          </ul>
        </div>
        <ImpactCard s={s} />
      </div>
      <GcpProofCard s={s} />
      <AuditCard audit={audit} />
      <StepNav prev="approval" go={go} />
    </>
  )
}

const EVENT_LABELS: Record<string, string> = {
  decision_created: 'Decision recorded', action_approved: 'Approved', action_rejected: 'Rejected',
  gmail_draft_created: 'Gmail draft created', calendar_followup_created: 'Calendar event created',
  outcome_verified: 'Outcome confirmed', release_readiness_updated: 'Readiness recomputed', readiness_assessed: 'Readiness assessed',
  execution_failed: 'Execution failed', execution_unavailable: 'Execution unavailable',
}

function AuditCard({ audit }: { audit: Audit | null }) {
  if (!audit) return null
  const rows = audit.events.filter((e) => EVENT_LABELS[e.event_type])
  return (
    <details className="card audit" data-testid="audit">
      <summary>
        <b>Audit trail · {rows.length} key events</b>{rows.length < audit.events.length ? <span className="meta"> (of {audit.events.length} recorded)</span> : null}{' '}
        <span className="meta">stored in {audit.store}{audit.bigquery.configured ? ` · each also streamed to BigQuery ${audit.bigquery.table}` : ''}</span>
      </summary>
      <div className="table-wrap">
        <table className="list-table">
          <thead><tr><th>When</th><th>Event</th><th>Action</th><th>By</th><th>Detail</th><th>Event id</th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.event_id || `${e.event_type}-${e.at}`}>
                <td>{fmtDateTime(e.at)}</td><td>{EVENT_LABELS[e.event_type]}</td><td><code>{e.action_id || '—'}</code></td>
                <td>{e.by.replace('Human reviewer as ', '') || '—'}</td><td>{e.detail}</td><td><code>{e.event_id.slice(0, 12)}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function ImpactCard({ s }: { s: Session }) {
  const [i, setImpact] = useState<Impact | null>(null)
  const [loading, setLoading] = useState(true)
  const p = s.progress
  // Recomputed whenever recorded work changes; the server reads the decision's full record.
  useEffect(() => {
    setLoading(true)
    api.impact(s.session_id).then((r) => setImpact(r.impact)).catch(() => setImpact(null)).finally(() => setLoading(false))
  }, [s.session_id, p.approved, p.rejected, p.executed, p.measured, s.contracts.length])
  if (loading && !i) return <div className="card impact"><h3>WorkPilot Impact</h3><p className="meta"><span className="spinner dark" /> Computing from the recorded workflow…</p></div>
  if (!i) return <div className="card impact"><h3>WorkPilot Impact</h3><p className="meta">Computed once a decision is recorded.</p></div>
  return (
    <div className="card impact" data-testid="impact">
      <div className="impact-head"><span>WORKPILOT IMPACT</span><Icon name="chart" size={22} /></div>
      <b className="impact-hours" data-testid="impact-hours">{i.hours_label}</b>
      <span className="impact-sub">Estimated time saved = manual baseline − WorkPilot run</span>
      <p className="impact-formula" data-testid="impact-formula">
        <span title="Formula-based estimate; the same number as on every other screen">{Math.round(i.baseline_minutes)} min manual baseline (estimate)</span>
        {' − '}
        <span title={`Server time of: ${i.workpilot_steps.join(', ') || 'no step yet'}`}>{seconds(i.workpilot_seconds * 1000)} WorkPilot run (measured)</span>
        {' = '}<b>{i.net_minutes} min</b> (formula-based estimate)
      </p>
      <div className="tiles" data-testid="impact-tiles">
        {i.tiles.map((t) => <div key={t.label} className={`tile ${t.tone}`}><b>{t.value}</b><span>{t.label}</span></div>)}
      </div>
      <details data-testid="impact-method">
        <summary>How this is computed</summary>
        <pre className="method">{i.methodology}</pre>
        {Object.keys(i.breakdown_minutes || {}).length ? (
          <table className="mini" data-testid="impact-breakdown"><tbody>
            {Object.entries(i.breakdown_minutes).map(([k, val]) => <tr key={k}><td>{k.replace(/_/g, ' ')}</td><td>{val} min</td></tr>)}
            <tr><td><b>manual baseline (estimate)</b></td><td><b>{i.baseline_minutes} min</b></td></tr>
            <tr><td>− WorkPilot run (measured)</td><td>{seconds(i.workpilot_seconds * 1000)}</td></tr>
            <tr><td><b>= estimated time saved</b></td><td><b>{i.net_minutes} min</b></td></tr>
          </tbody></table>
        ) : null}
        <p className="meta">Computed {fmtDateTime(i.computed_at)}</p>
      </details>
    </div>
  )
}
