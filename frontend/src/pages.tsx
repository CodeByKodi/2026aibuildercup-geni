import { useEffect, useState } from 'react'
import { api, rememberedSessions, type MemoryEntry, type SessionSummary, type StatusRow } from './api'
import { Badge, Empty, fmtDateTime } from './ui'

const STAGE_LABEL: Record<string, string> = {
  KNOWLEDGE: 'Searching', ANALYZE: 'Evidence found', ACTIONS: 'Decision recorded', APPROVAL: 'Waiting for approval',
  OUTCOME: 'Waiting for outcomes', IMPACT: 'Outcomes recorded',
}

function useLoad<T>(fn: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { fn().then(setData).catch((e) => setError(e.message)) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error }
}

export function SessionsPage({ current }: { current: string }) {
  const ids = rememberedSessions()
  const { data, error } = useLoad(() => (ids.length ? api.sessions(ids) : Promise.resolve([] as SessionSummary[])))
  return (
    <>
      <div className="panel-head"><div><h2>My Sessions</h2><p className="sub">Analyses started in this browser. Each one is saved in Firestore and opens from its link.</p></div></div>
      {error ? <div className="alert error">{error}</div> : null}
      {data && !data.length ? <Empty title="No sessions yet">Start a new analysis; it will be listed here.</Empty> : null}
      {data?.length ? (
        <div className="table-wrap">
          <table className="list-table" data-testid="sessions-table">
            <thead><tr><th>Question</th><th>Stage</th><th>Decision</th><th>Updated</th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.session_id}>
                  <td><a href={`/?s=${r.session_id}`}>{r.question || r.session_id}</a>{r.session_id === current ? <Badge tone="progress">Open</Badge> : null}</td>
                  <td>{STAGE_LABEL[r.stage] ?? r.stage}{r.readiness ? <> <Badge>{r.readiness}</Badge></> : null}</td>
                  <td>{r.decision_id ? <code>{r.decision_id}</code> : '—'}{r.contracts ? ` · ${r.contracts} contracts` : ''}</td>
                  <td>{fmtDateTime(r.updated_at || r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  )
}

export function MemoryPage() {
  const { data, error } = useLoad(async () => {
    const ids = rememberedSessions()
    if (!ids.length) return [] as MemoryEntry[]
    const decisions = (await api.sessions(ids)).map((r) => r.decision_id).filter(Boolean)
    return decisions.length ? api.memory(decisions) : []
  })
  return (
    <>
      <div className="panel-head"><div><h2>Decision Memory</h2><p className="sub">Why each decision was made, its evidence, who approved the actions, and expected versus actual outcome.</p></div></div>
      {error ? <div className="alert error">{error}</div> : null}
      {data && !data.length ? <Empty title="No decisions yet">A completed analysis records its first Decision Record here.</Empty> : null}
      {data?.map((m) => (
        <details key={m.decision_id} className="card memory" data-testid="memory-entry" open={data.length === 1}>
          <summary><code>{m.decision_id}</code> <b>{m.question}</b> <Badge>{m.status}</Badge></summary>
          <h3>Finding</h3><p>{m.finding}</p>
          <h3>Recommendation</h3><p>{m.recommendation}</p>
          {m.rationale ? <><h3>Rationale</h3><p>{m.rationale}</p></> : null}
          <h3>Evidence</h3>
          <ul>{m.evidence.map((e, i) => <li key={i}><b>{e.source}</b> — {e.excerpt}</li>)}</ul>
          <h3>Actions</h3>
          <ul>{m.actions.map((a) => <li key={a.action_id}>{a.title} · {a.approval} · {a.execution} · outcome {a.outcome_status.replace(/_/g, ' ').toLowerCase()}</li>)}</ul>
          <p className="meta">Approved by {m.approved_by} · Expected: {m.expected_outcome || 'not recorded'} · Actual: {m.actual_outcome || 'not recorded yet'} · {fmtDateTime(m.created_at)}</p>
        </details>
      ))}
    </>
  )
}

export function StatusPage() {
  const { data, error } = useLoad(api.status)
  return (
    <>
      <div className="panel-head"><div><h2>System Status</h2><p className="sub">Each Google Cloud service as this deployment sees it right now.</p></div></div>
      {error ? <div className="alert error">{error}</div> : null}
      {data ? (
        <div className="table-wrap">
          <table className="list-table" data-testid="status-table">
            <thead><tr><th>Technology</th><th>Status</th><th>Detail</th><th>Used for</th></tr></thead>
            <tbody>
              {data.rows.map((r: StatusRow) => (
                <tr key={r.technology}>
                  <td><b>{r.technology}</b></td>
                  <td><Badge tone={/ACTIVE|CONNECTED|OK|LIVE/.test(r.status) ? 'done' : /NOT USED/.test(r.status) ? 'muted' : 'warn'}>{r.status}</Badge></td>
                  <td>{r.detail}</td><td>{r.use}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="meta">Checked {fmtDateTime(data.checked_at)}</p>
        </div>
      ) : null}
    </>
  )
}
