import { useEffect, useState } from 'react'
import { api, type Config, type Impact, type Proof, type Session } from './api'
import { Badge, Icon, fmtBytes, fmtDateTime, seconds } from './ui'

/** The decision's impact, re-read whenever recorded work changes. */
export function useImpact(s: Session | null) {
  const [impact, setImpact] = useState<Impact | null>(null)
  const [loading, setLoading] = useState(false)
  const p = s?.progress
  const decided = !!s?.analysis?.decision_id
  useEffect(() => {
    if (!s?.session_id || !decided) { setImpact(null); return }
    setLoading(true)
    api.impact(s.session_id).then((r) => setImpact(r.impact)).catch(() => setImpact(null)).finally(() => setLoading(false))
  }, [s?.session_id, decided, p?.approved, p?.rejected, p?.executed, p?.measured, s?.contracts.length, s?.agent_message?.at])
  return { impact, loading }
}

/** Google Cloud proof for this session, re-read whenever recorded work changes. */
export function useProof(s: Session | null) {
  const [proof, setProof] = useState<Proof | null>(null)
  const p = s?.progress
  useEffect(() => {
    if (!s?.session_id) { setProof(null); return }
    api.proof(s.session_id).then(setProof).catch(() => setProof(null))
  }, [s?.session_id, s?.retrieved?.question, s?.analysis?.decision_id, p?.approved, p?.executed, p?.measured, s?.agent_message?.at, s?.sources.length])
  return proof
}

function Cell({ k, v, hint, tone, testid }: { k: string; v: string; hint?: string; tone?: string; testid?: string }) {
  return (
    <div className={`proof-cell${tone ? ` ${tone}` : ''}`} title={hint} data-testid={testid}>
      <b>{v}</b><span>{k}</span>
    </div>
  )
}

/** Impact & Proof strip: every number is read from this session's recorded state. "—" means not yet. */
export function ProofStrip({ s, cfg }: { s: Session | null; cfg: Config }) {
  const { impact } = useImpact(s)
  const a = s?.analysis?.grounded ? s.analysis : null
  const executed = s?.contracts.filter((c) => c.execution === 'Executed') ?? []
  const unapproved = executed.filter((c) => !c.approved_by).length
  const docs = s ? s.sources.length : cfg.sources.length
  return (
    <div className="proof-strip" data-testid="proof-strip" aria-label="Impact and proof for this session">
      <Cell k="docs searched" v={String(docs)} hint="Workspace documents plus your uploads" testid="ps-docs" />
      <Cell k="cited passages" v={s?.retrieved ? String(s.retrieved.evidence.length) : '—'} hint="Passages the search returned for this question" testid="ps-passages" />
      <Cell k="analysis" v={a?.duration_ms ? seconds(a.duration_ms) : '—'} hint="The agent's analysis, timed on the server" testid="ps-analysis" />
      <Cell k="blockers" v={a ? String(a.stats.blockers) : '—'} testid="ps-blockers" />
      <Cell k="owners" v={a ? String(a.stats.owners) : '—'} testid="ps-owners" />
      <Cell k="approval conditions" v={a ? String(a.stats.conditions) : '—'} testid="ps-conditions" />
      <Cell k="manual baseline (estimate)" testid="ps-manual" v={impact ? `${Math.round(impact.baseline_minutes)} min` : '—'}
        hint={impact ? `Formula-based estimate, fixed once the decision is recorded. Estimated time saved = manual baseline − WorkPilot run = ${impact.hours_label}. See How this is computed on Outcome & Impact.` : 'Shown once a decision is recorded'} />
      <Cell k="WorkPilot run (measured)" testid="ps-workpilot" v={impact ? seconds(impact.workpilot_seconds * 1000) : '—'}
        hint={impact ? `Server time of: ${impact.workpilot_steps.join(', ') || 'no step yet'}` : 'Shown once a decision is recorded'} />
      <Cell k="executed without human approval" v={`${unapproved}`} tone={unapproved ? 'bad' : 'good'} testid="ps-gate"
        hint={`${executed.length} action(s) executed, each after a recorded human approval`} />
    </div>
  )
}

/** Everything WorkPilot has for one cited source: the document, the exact passages, the claims it supports. */
export function EvidenceDrawer({ s, source, onClose }: { s: Session; source: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const doc = s.sources.find((x) => x.name === source)
  const passages = (s.retrieved?.evidence || []).filter((e) => e.source === source)
  const claims = (s.analysis?.sections.blockers || []).filter((b) => b.evidence.includes(source))
  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`Evidence from ${source}`} onClick={(e) => e.stopPropagation()} data-testid="evidence-drawer">
        <div className="drawer-head">
          <div>
            <span className="label">Cited source</span>
            <h3>{doc?.title || source}</h3>
            <code className="tag">{source}</code>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        {doc ? (
          <p className="meta" data-testid="drawer-doc">
            {doc.type} · {doc.words.toLocaleString()} words · {fmtBytes(doc.bytes)}{doc.chunks ? ` · ${doc.chunks} ${doc.chunks === 1 ? 'section' : 'sections'}` : ''}
            {doc.as_of ? ` · document dated ${doc.as_of}` : ''}{doc.uploaded ? ` · uploaded, read by ${doc.parser}` : ''}
          </p>
        ) : null}
        <h4>Claims this source supports</h4>
        {claims.length ? (
          <ul className="claims" data-testid="drawer-claims">{claims.map((b, i) => <li key={i}><b>{b.blocker}</b>{b.business_impact ? <span className="meta">{b.business_impact}</span> : null}</li>)}</ul>
        ) : <p className="meta">No blocker in the decision cites this source.</p>}
        <h4>Retrieved passages · {passages.length}</h4>
        {passages.length ? passages.map((e) => (
          <div key={e.rank} className="passage" data-testid="drawer-passage">
            <div className="meta">
              Rank {e.rank} · {e.locator}{e.page ? ` · chunk ${e.page}` : ''}{Number(e.score) > 0 ? ` · relevance ${e.score}` : ''} · {s.retrieved?.mode}
            </div>
            <blockquote>{e.excerpt}</blockquote>
          </div>
        )) : <p className="meta" data-testid="drawer-no-passage">The agent cited this source, but none of its passages is in the set retrieved for this question.</p>}
      </aside>
    </div>
  )
}

/** A source chip that opens its evidence. */
export function Cite({ source, label, onCite }: { source: string; label?: string; onCite: (source: string) => void }) {
  return (
    <button className="tag cite" onClick={() => onCite(source)} title="Show the evidence from this source" data-testid="cite">
      <Icon name="doc" size={12} /> {label || source}
    </button>
  )
}

/** What each Google Cloud service did for this session. */
export function GcpProofCard({ s }: { s: Session }) {
  const proof = useProof(s)
  if (!proof) return null
  return (
    <details className="card gcp-proof" data-testid="gcp-proof">
      <summary>
        <b>Google Cloud proof for this session</b>{' '}
        <span className="meta">Firestore ids · decision <code>{proof.ids.decision || '—'}</code> · {proof.ids.actions.length} actions · {proof.events} audit events</span>
      </summary>
      <div className="table-wrap">
        <table className="list-table">
          <thead><tr><th>Service</th><th>Resource</th><th>Used for</th><th>Last call</th><th>Trace id</th><th>Detail</th></tr></thead>
          <tbody>
            {proof.rows.map((r) => (
              <tr key={r.service} data-testid="gcp-row">
                <td><b>{r.service}</b></td>
                <td><code>{r.resource}</code></td>
                <td>{r.used_for}</td>
                <td>{r.last?.at ? <>{fmtDateTime(r.last.at)}{r.last.ms != null ? ` · ${seconds(r.last.ms)}` : ''}</> : '—'}</td>
                <td>{r.last?.trace ? <code>{r.last.trace.slice(0, 16)}</code> : '—'}</td>
                <td>{r.count != null ? <Badge tone="done">{r.count} BigQuery rows for this decision</Badge> : null} {r.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="meta">Session <code>{proof.ids.session}</code> · actions {proof.ids.actions.map((a) => <code key={a}>{a} </code>)}· trace ids are Cloud Run request traces. Executed: {proof.executed}; without a recorded approver: {proof.unapproved_executions}.</p>
    </details>
  )
}
