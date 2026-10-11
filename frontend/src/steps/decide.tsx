import { StepNav, type StepProps } from '../App'
import { api } from '../api'
import { Avatar, Badge, Empty, Icon, fmtDate } from '../ui'
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
export function DecisionStep({ s, go, run, busy }: StepProps) {
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
                <li key={row.source}><Icon name="doc" size={16} /><span>{row.title}</span><span className="supports">{row.supports.map((x) => <code key={x} className="tag">{x}</code>)}</span></li>
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
export function ActionsStep({ s, go, run, busy }: StepProps) {
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
        <div><h2>Action Contracts</h2><p className="sub">Proposed actions to address the blockers. Nothing runs until a person approves it.</p></div>
        <ProducedBy mode={s.contracts_mode || s.analysis.ai_mode} />
      </div>
      <div className="contracts">
        {s.contracts.map((c) => (
          <article key={c.action_id} className={`contract p-${c.priority.toLowerCase()}`} data-testid="contract">
            <div className="contract-main">
              <div className="contract-head"><Icon name="alert" size={16} /><b>{c.title}</b><Badge>{c.priority}</Badge></div>
              {c.rationale ? <p>{c.rationale}</p> : null}
              <div className="kv">
                <div><span>Owner</span><span className="who"><Avatar name={c.owner} initials={c.initials} /> {c.owner}</span></div>
                <div><span>Due</span><span><Icon name="calendar" size={14} /> {c.due_date}{c.due_label ? <em className="due"> · {c.due_label}</em> : null}</span></div>
                <div><span>Approval</span><Badge>{c.approval}</Badge></div>
              </div>
            </div>
            <div className="contract-side">
              <span className="label">Expected Outcome</span>
              <p>{c.expected_outcome || 'Not stated'}</p>
              <span className="label">Evidence</span>
              <p>{c.evidence.join(', ') || 'None cited'}</p>
            </div>
          </article>
        ))}
      </div>
      <StepNav prev="decision" go={go} next={{ label: 'Next: Approval', to: 'approval', testid: 'next-approval' }} />
    </>
  )
}
