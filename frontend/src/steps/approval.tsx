import { useState } from 'react'
import { StepNav, type StepProps } from '../App'
import { api, type Contract, type Session } from '../api'
import { Avatar, Badge, Button, Empty, Icon, fmtDateTime } from '../ui'

export function AgentBanner({ s }: { s: Session }) {
  const mode = s.agent.mode
  return (
    <div className={`agent-banner ${mode}`} data-testid="agent-status" data-mode={mode}>
      <Icon name={mode === 'paused' ? 'pause' : mode === 'ready' ? 'play' : 'spark'} size={18} />
      <span>{s.agent.text}</span>
    </div>
  )
}

function Receipt({ c }: { c: Contract }) {
  if (!c.receipt) return null
  const ref = c.receipt.reference
  return (
    <span className="receipt" data-testid="receipt">
      <Icon name={c.receipt.kind.startsWith('Gmail') ? 'mail' : 'calendar'} size={14} /> {c.receipt.kind} ·{' '}
      {ref.startsWith('https://') ? <a href={ref} target="_blank" rel="noreferrer">open</a> : <code>{ref}</code>} · {fmtDateTime(c.receipt.at)}
    </span>
  )
}

function Row({ c, s, run, busy }: { c: Contract; s: Session; run: StepProps['run']; busy: string }) {
  const [editing, setEditing] = useState(false)
  const [owner, setOwner] = useState(c.owner)
  const [due, setDue] = useState(c.due_date)
  const sid = s.session_id
  const pending = c.approval === 'Pending'
  const approved = c.approval === 'Approved'
  const executed = c.execution === 'Executed'
  const key = (k: string) => `${k}:${c.action_id}`
  const offline = !s.followups_connected
  return (
    <>
      <tr data-testid="approval-row" data-action={c.action_id} data-approval={c.approval} data-execution={c.execution}>
        <td>
          <b>{c.title}</b>
          <span className="meta">Due {c.due_date}{c.due_label ? ` · ${c.due_label}` : ''}</span>
          {approved && c.approved_by ? <span className="meta">Approved by {c.approved_by.replace('Human reviewer as ', '')} · {fmtDateTime(c.approved_at)}</span> : null}
        </td>
        <td><span className="who"><Avatar name={c.owner} initials={c.initials} /> <span className="owner-name">{c.owner}</span></span></td>
        <td><Badge>{c.priority}</Badge></td>
        <td><Badge>{c.approval}</Badge></td>
        <td className="exec-cell">
          {executed ? <><Badge>Executed</Badge><Receipt c={c} /></> : approved ? (
            <div className="exec-btns">
              <Button kind="secondary" icon="mail" disabled={offline} busy={busy === key('gmail')} testid="run-gmail"
                title={offline ? 'Gmail is not connected; execution stays Not started' : 'Create a Gmail draft for the owner (not sent)'}
                onClick={() => run(key('gmail'), () => api.execute(sid, c.action_id, 'gmail_draft'))}>Gmail draft</Button>
              <Button kind="secondary" icon="calendar" disabled={offline} busy={busy === key('cal')} testid="run-calendar"
                title={offline ? 'Calendar is not connected; execution stays Not started' : 'Create a Calendar event on the due date'}
                onClick={() => run(key('cal'), () => api.execute(sid, c.action_id, 'calendar_followup'))}>Calendar event</Button>
            </div>
          ) : <span className="meta">{c.execution}</span>}
        </td>
        <td className="decide-cell">
          {pending ? (
            <div className="decide-btns">
              <button className="icon-btn ok" aria-label={`Approve ${c.title}`} disabled={!!busy} onClick={() => run(key('approve'), () => api.approve(sid, c.action_id))} data-testid="approve">
                {busy === key('approve') ? <span className="spinner dark" /> : <Icon name="check" />}
              </button>
              <button className="icon-btn no" aria-label={`Reject ${c.title}`} disabled={!!busy} onClick={() => run(key('reject'), () => api.reject(sid, c.action_id))} data-testid="reject">
                {busy === key('reject') ? <span className="spinner dark" /> : <Icon name="x" />}
              </button>
              <button className="link-btn" onClick={() => setEditing((e) => !e)}>{editing ? 'Close' : 'Edit'}</button>
            </div>
          ) : <span className="meta">{c.approval}</span>}
        </td>
      </tr>
      {editing && pending ? (
        <tr className="edit-row">
          <td colSpan={6}>
            <label>Owner <input value={owner} onChange={(e) => setOwner(e.target.value)} /></label>
            <label>Due date <input value={due} onChange={(e) => setDue(e.target.value)} placeholder="YYYY-MM-DD" /></label>
            <Button kind="secondary" busy={busy === key('edit')} onClick={() => run(key('edit'), async () => { const out = await api.fields(sid, c.action_id, owner, due); setEditing(false); return out })}>Save changes</Button>
          </td>
        </tr>
      ) : null}
    </>
  )
}

// --------------------------------------------------------------------------
// 6 · Approval (human review, then the approved work is run by a person)
// --------------------------------------------------------------------------
export function ApprovalStep({ s, go, run, busy }: StepProps) {
  const [tab, setTab] = useState<'All' | 'Approved' | 'Pending' | 'Rejected'>('All')
  if (!s?.contracts.length) return <Empty title="Nothing to review">Prepare Action Contracts first.</Empty>
  const p = s.progress
  const rows = s.contracts.filter((c) => tab === 'All' || c.approval === tab)
  const count = { All: p.total, Approved: p.approved, Pending: p.pending, Rejected: p.rejected }
  const waitingRun = p.approved - p.executed
  const plural = (n: number) => `${n} approved action${n === 1 ? '' : 's'}`
  const runNote = p.approved === 0
    ? 'No approved actions ready to run yet'
    : p.executed === 0
      ? `${plural(p.approved)} ready to run`
      : `${p.executed} of ${plural(p.approved)} run${waitingRun > 0 ? ` · ${waitingRun} ready to run` : ''}`
  return (
    <>
      <div className="panel-head">
        <div><h2>Review and Approve Actions</h2><p className="sub">Approve or reject each proposed action, then run the approved ones. The agent cannot do either.</p></div>
      </div>
      <AgentBanner s={s} />
      <div className="tabs" role="tablist" data-testid="approval-tabs">
        {(['All', 'Approved', 'Pending', 'Rejected'] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} data-testid={`tab-${t.toLowerCase()}`}>{t === 'All' ? 'All Actions' : t} ({count[t]})</button>
        ))}
        <span className="tab-note" data-testid="executed-count">{runNote}</span>
      </div>
      <div className="bulk-row">
        {p.pending > 0 ? (
          <Button kind="secondary" icon="check" busy={busy === 'approve-all'} disabled={!!busy} testid="approve-all"
            title="Records your approval on every pending contract, one by one"
            onClick={() => run('approve-all', async () => {
              let out: Session | undefined
              for (const c of s.contracts.filter((x) => x.approval === 'Pending')) out = await api.approve(s.session_id, c.action_id)
              return out
            })}>Approve all pending ({p.pending})</Button>
        ) : null}
        {waitingRun > 0 ? (
          <Button icon="mail" busy={busy === 'run-all'} disabled={!!busy || !s.followups_connected} testid="run-all"
            title={s.followups_connected ? 'Creates one Gmail draft per approved action in the connected mailbox. Nothing is sent.' : 'Gmail is not connected on this deployment'}
            onClick={() => run('run-all', () => api.executeApproved(s.session_id, 'gmail_draft'))}>
            Run {waitingRun} approved as Gmail drafts
          </Button>
        ) : null}
        {waitingRun > 0 && s.followups_connected ? <span className="meta">Drafts are created in the team's connected mailbox and never sent; each receipt is the ID Gmail returns.</span> : null}
      </div>
      <div className="table-wrap">
        <table className="approval-table">
          <thead><tr><th>Action</th><th>Owner</th><th>Priority</th><th>Approval</th><th>Execution</th><th>Decision</th></tr></thead>
          <tbody>{rows.map((c) => <Row key={c.action_id} c={c} s={s} run={run} busy={busy} />)}</tbody>
        </table>
      </div>
      {!s.followups_connected && p.approved > 0 ? (
        <div className="alert warn">Gmail and Calendar are not connected on this deployment, so approved actions cannot run and outcomes cannot be recorded.</div>
      ) : null}
      <StepNav prev="actions" go={go} next={{
        label: 'Next: Outcome', to: 'outcome', testid: 'next-outcome', disabled: p.pending > 0,
        title: p.pending > 0 ? `${p.pending} action(s) still need a decision` : waitingRun > 0 ? `${waitingRun} approved action(s) not run yet` : undefined,
      }} />
    </>
  )
}
