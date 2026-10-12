import { useState } from 'react'
import { StepNav, type StepProps } from '../App'
import { api, type AuditEvent, type Contract, type Preview, type Session } from '../api'
import { Avatar, Badge, Button, Empty, Icon, fmtDate, fmtDateTime } from '../ui'
import { Cite, GcpProofCard } from '../proof'
import { ProducedBy } from './early'

/** The record's headline is the recorded verdict, never new wording from the UI. */
function verdictTitle(status: string): string {
  const s = (status || '').toUpperCase()
  if (s === 'READY FOR APPROVAL') return 'Ready for go-live approval'
  if (s === 'AT RISK') return 'Go-live is at risk'
  if (s === 'NOT READY') return 'Release is not ready for go-live'
  return status || 'Recorded'
}

// --------------------------------------------------------------------------
// 4 · Decision
// --------------------------------------------------------------------------
export function DecisionStep({ s, go, run, busy, cite }: StepProps) {
  const a = s?.analysis
  if (!s || !a?.grounded) return <Empty title="No decision yet">Run the analysis first.</Empty>
  const v = a.verdict || a.initial_verdict
  const hasContracts = s.contracts.length > 0
  const prepare = () => run('contracts', async () => {
    const out = await api.contracts(s.session_id)
    go('actions')
    return out
  })
  return (
    <>
      <div className="panel-head">
        <div><h2>Decision Record</h2><p className="sub">What the agent recommends, why, and what must be true before approval.</p></div>
        <ProducedBy mode={a.ai_mode} />
      </div>
      <div className={`record tone-${v?.tone ?? 'red'}`} data-testid="decision-record">
        <span className="verdict-icon"><Icon name={v?.tone === 'green' ? 'check' : 'alert'} size={26} /></span>
        <div className="record-body">
          <b className="record-title" data-testid="decision-title">Decision: {verdictTitle(v?.status ?? a.sections.readiness_status)}</b>
          {a.sections.recommendation ? <p><b>Recommendation:</b> {a.sections.recommendation}</p> : null}
          {a.sections.finding ? <p className="meta">{a.sections.finding}</p> : null}
          <div className="record-meta">
            <div><span>Status</span><Badge>{v?.status ?? a.sections.readiness_status}</Badge></div>
            <div><span>Decision ID</span><code data-testid="decision-id">{a.decision_id}</code></div>
            <div><span>Created</span><span>{fmtDate(a.created_at)}</span></div>
          </div>
        </div>
      </div>
      <div className="two-col">
        <div className="card">
          <h3>Rationale</h3>
          <p>{a.sections.rationale || 'The agent did not record a separate rationale.'}</p>
          {a.sections.expected_outcome ? <><h3>Expected outcome</h3><p>{a.sections.expected_outcome}</p></> : null}
        </div>
        <div className="card">
          <h3>Key Evidence</h3>
          {a.evidence_map.length ? (
            <ul className="ev-map" data-testid="evidence-map">
              {a.evidence_map.map((row) => (
                <li key={row.source}><Icon name="doc" size={16} /><Cite source={row.source} label={row.title} onCite={cite} /><span className="supports">{row.supports.map((x) => <code key={x} className="tag">{x}</code>)}</span></li>
              ))}
            </ul>
          ) : <p className="meta">No sources were cited on the blockers.</p>}
        </div>
      </div>
      <div className="card">
        <h3>Approval Conditions ({a.sections.approval_conditions.length})</h3>
        <ol className="conditions" data-testid="approval-conditions">
          {a.sections.approval_conditions.map((c) => <li key={c}>{c}</li>)}
        </ol>
        {a.sections.missing_information ? <p className="meta">Missing information: {a.sections.missing_information}</p> : null}
      </div>
      <GcpProofCard s={s} />
      <StepNav prev="analyze" go={go} next={
        hasContracts
          ? { label: 'Next: Actions', to: 'actions', testid: 'next-actions' }
          : { label: 'Prepare Action Contracts', onClick: prepare, busy: busy === 'contracts', testid: 'prepare-contracts' }
      } />
    </>
  )
}

// --------------------------------------------------------------------------
// 5 · Actions
// --------------------------------------------------------------------------
const EVENT_LABELS: Record<string, string> = {
  action_contract_created: 'Proposed by the agent', action_approved: 'Approved', action_rejected: 'Rejected',
  gmail_draft_created: 'Gmail draft created', calendar_followup_created: 'Calendar event created',
  execution_failed: 'Execution failed', execution_unavailable: 'Execution unavailable', outcome_verified: 'Outcome confirmed',
}

function ContractCard({ c, s, onCite }: { c: Contract; s: Session; onCite: (source: string) => void }) {
  const [open, setOpen] = useState<'' | 'gmail' | 'calendar' | 'audit'>('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [error, setError] = useState('')
  const show = async (what: 'gmail' | 'calendar' | 'audit') => {
    if (open === what) { setOpen(''); return }
    setError('')
    try {
      if (what === 'audit') setEvents((await api.audit(s.session_id)).events.filter((e) => e.action_id === c.action_id && EVENT_LABELS[e.event_type]))
      else if (!preview) setPreview(await api.preview(s.session_id, c.action_id))
      setOpen(what)
    } catch (e) { setError((e as Error).message) }
  }
  const executed = c.execution === 'Executed'
  return (
    <article className={`contract p-${c.priority.toLowerCase()}`} data-testid="contract">
      <div className="contract-main">
        <div className="contract-head"><Icon name="alert" size={16} /><b>{c.title}</b><Badge>{c.priority}</Badge></div>
        <div className="kv">
          <div><span>Owner</span><span className="who"><Avatar name={c.owner} initials={c.initials} /> {c.owner}</span></div>
          <div><span>Due</span><span><Icon name="calendar" size={14} /> {c.due_date}{c.due_label ? <em className="due"> · {c.due_label}</em> : null}</span></div>
          <div><span>Approval</span><Badge>{c.approval}</Badge></div>
          <div><span>Execution</span><Badge>{c.execution}</Badge></div>
        </div>
        <span className="label">Blocker it resolves</span>
        <p>{c.rationale || 'Not stated'}</p>
        <span className="label">Precondition</span>
        <p data-testid="precondition">{executed
          ? `Ran after ${c.approved_by.replace('Human reviewer as ', '') || 'a person'} approved it${c.approved_at ? ` (${fmtDateTime(c.approved_at)})` : ''}.`
          : c.approval === 'Approved' ? 'Approved by a person. Runs only when a person clicks Run on Approval.'
          : c.approval === 'Rejected' ? 'Rejected by a person. It will not run.'
          : 'Runs only after a person approves it on Approval. Nothing has been sent.'}</p>
      </div>
      <div className="contract-side">
        <span className="label">Expected outcome</span>
        <p>{c.expected_outcome || 'Not stated'}</p>
        <span className="label">Evidence</span>
        <div className="ev-chips">{c.evidence_sources.length ? c.evidence_sources.map((e, i) => <Cite key={e} source={e} label={c.evidence[i]} onCite={onCite} />) : <Badge tone="warn">No cited source</Badge>}</div>
        <div className="preview-btns">
          <Button kind="secondary" icon="mail" onClick={() => show('gmail')} testid="preview-gmail">{open === 'gmail' ? 'Hide' : 'Gmail draft preview'}</Button>
          <Button kind="secondary" icon="calendar" onClick={() => show('calendar')} testid="preview-calendar">{open === 'calendar' ? 'Hide' : 'Calendar preview'}</Button>
          <Button kind="ghost" icon="memory" onClick={() => show('audit')} testid="preview-audit">{open === 'audit' ? 'Hide' : 'Audit log'}</Button>
        </div>
      </div>
      {error ? <p className="error wide">{error}</p> : null}
      {open === 'gmail' && preview ? (
        <div className="preview wide" data-testid="gmail-preview">
          <div className="preview-meta"><b>Gmail draft</b> · to <code>{preview.gmail.to}</code> · {executed ? 'created as a draft (never sent)' : 'not created yet: built locally, nothing sent to Google'}</div>
          <div className="preview-subject">{preview.gmail.subject}</div>
          <pre>{preview.gmail.body}</pre>
        </div>
      ) : null}
      {open === 'calendar' && preview ? (
        <div className="preview wide" data-testid="calendar-preview">
          <div className="preview-meta"><b>Calendar event</b> · {preview.calendar.date} · attendee <code>{preview.calendar.attendee}</code> · {preview.connected ? 'created only if a person runs it as a Calendar event' : 'Calendar not connected'}</div>
          <div className="preview-subject">{preview.calendar.title}</div>
          <pre>{preview.calendar.description}</pre>
        </div>
      ) : null}
      {open === 'audit' && events ? (
        <div className="preview wide" data-testid="audit-preview">
          <div className="preview-meta"><b>Audit log</b> · action <code>{c.action_id}</code> · Firestore audit events</div>
          {events.length ? (
            <ul className="audit-list">{events.map((e) => (
              <li key={e.event_id}><span>{fmtDateTime(e.at)}</span><b>{EVENT_LABELS[e.event_type]}</b><span>{e.by.replace('Human reviewer as ', '')}</span><code>{e.event_id.slice(0, 12)}</code></li>
            ))}</ul>
          ) : <p className="meta">No events for this action yet.</p>}
        </div>
      ) : null}
    </article>
  )
}

export function ActionsStep({ s, go, run, busy, cite }: StepProps) {
  if (!s?.analysis?.grounded) return <Empty title="No decision yet">Run the analysis first.</Empty>
  if (!s.contracts.length) {
    return (
      <>
        <Empty title="No Action Contracts yet">The agent proposes one contract per blocker from the same evidence.</Empty>
        <StepNav prev="decision" go={go} next={{ label: 'Prepare Action Contracts', busy: busy === 'contracts', testid: 'prepare-contracts', onClick: () => run('contracts', () => api.contracts(s.session_id)) }} />
      </>
    )
  }
  return (
    <>
      <div className="panel-head">
        <div><h2>Action Contracts</h2><p className="sub">Proposed actions to address the blockers. Nothing runs until a person approves it; preview exactly what would be sent.</p></div>
        <ProducedBy mode={s.contracts_mode || s.analysis.ai_mode} />
      </div>
      <div className="contracts">
        {s.contracts.map((c) => <ContractCard key={c.action_id} c={c} s={s} onCite={cite} />)}
      </div>
      <StepNav prev="decision" go={go} next={{ label: 'Next: Approval', to: 'approval', testid: 'next-approval' }} />
    </>
  )
}
