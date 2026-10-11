import { useEffect, useState } from 'react'
import { StepNav, type StepProps } from '../App'
import { api, type Blocker, type Session } from '../api'
import { Avatar, Badge, Button, Empty, Icon, fmtBytes, seconds } from '../ui'
import { openLine } from './outcome'

/** "AI-generated" only when Gemini actually produced it; otherwise the real mode. */
export function ProducedBy({ mode }: { mode: string }) {
  if (!mode) return null
  const ai = /^Gemini on Vertex AI/.test(mode)
  return <span className={`produced ${ai ? 'ai' : 'fallback'}`} data-testid="produced-by"><Icon name={ai ? 'spark' : 'alert'} size={14} /> {mode}</span>
}

const ACCEPT = '.pdf,.docx,.txt,.md,.csv,.png,.jpg,.jpeg,.tif,.tiff'

/** Add a person's own documents to this session. Parsed on the server; nothing is indexed for anyone else. */
export function Uploads({ s, busy, run, ensureSession, question, scenarioId, onAdded }: StepProps & {
  question?: string; scenarioId?: string; onAdded?: () => void
}) {
  const uploads = (s?.sources || []).filter((x) => x.uploaded)
  const locked = !!s?.analysis
  const add = (files: FileList | null) => {
    if (!files?.length) return
    const list = Array.from(files)
    run('upload', async () => {
      const sess = await ensureSession(question, scenarioId)
      let out: Session = sess
      for (const f of list) out = await api.upload(sess.session_id, f)
      onAdded?.()
      return out
    })
  }
  return (
    <div className="uploads" data-testid="uploads">
      <div className="uploads-head">
        <div>
          <b>Your documents</b>
          <span className="meta">PDF, Word, images or text, up to 20 MB · searched with the workspace in this session only · scanned PDFs and images are read by Document AI when it is connected</span>
        </div>
        {locked ? null : (
          <label className={`btn secondary${busy === 'upload' ? ' disabled' : ''}`}>
            {busy === 'upload' ? <span className="spinner dark" /> : <Icon name="plus" size={16} />} Add documents
            <input type="file" multiple accept={ACCEPT} className="sr-only" disabled={busy === 'upload'}
              onChange={(e) => { add(e.target.files); e.target.value = '' }} data-testid="upload-input" />
          </label>
        )}
      </div>
      {uploads.length ? (
        <ul className="upload-list">
          {uploads.map((u) => (
            <li key={u.name} data-testid="upload-row">
              <Icon name="doc" size={16} />
              <span><b>{u.name}</b> <span className="meta">{u.type} · {fmtBytes(u.bytes)} · {u.words.toLocaleString()} words · read by {u.parser || 'local parser'}{u.truncated ? ' · long file, first part kept' : ''}</span></span>
              {locked ? null : (
                <button className="icon-btn no" aria-label={`Remove ${u.name}`} disabled={!!busy}
                  onClick={() => run('upload', () => api.removeUpload(s!.session_id, u.source_id || ''))}><Icon name="x" size={14} /></button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

// --------------------------------------------------------------------------
// 1 · Start
// --------------------------------------------------------------------------
export function StartStep(props: StepProps) {
  const { s, cfg, busy, begin } = props
  const [question, setQuestion] = useState(s?.question || cfg.default_question)
  const [scenarioId, setScenarioId] = useState(s?.scenario_id || 'go-live')
  const pick = (id: string) => {
    const item = cfg.scenarios.find((x) => x.id === id)
    if (!item) return
    setScenarioId(id)
    setQuestion(item.question)
  }
  const persona = cfg.scenarios.find((x) => x.id === scenarioId)?.persona
  return (
    <div className="start">
      <div className="hero">
        <h1>Turn enterprise knowledge into decisions and outcomes.</h1>
        <p>Ask a question, get evidence-backed insights, actionable plans, and measure the impact.</p>
      </div>
      <div className="chips" role="group" aria-label="Sample scenarios">
        {cfg.scenarios.map((x) => (
          <button key={x.id} className={`chip${x.id === scenarioId ? ' on' : ''}`} onClick={() => pick(x.id)} title={x.hint} data-testid={`scenario-${x.id}`}>
            <Icon name={x.id === 'go-live' ? 'bolt' : x.id === 'sec-217' ? 'shield' : 'chart'} size={15} /> {x.label}
          </button>
        ))}
      </div>
      <label className="sr-only" htmlFor="question">Your question</label>
      <textarea id="question" className="question" rows={4} value={question} onChange={(e) => setQuestion(e.target.value)} data-testid="question" />
      <div className="start-row">
        <div className="workspace-pill" title="The sample workspace this demo searches">
          <Icon name="doc" size={16} /> {cfg.scenario_title} · {cfg.sources.length} sources{persona ? ` · asking as ${persona}` : ''}
        </div>
        <Button onClick={() => begin(question.trim() || cfg.default_question, scenarioId)} busy={busy === 'begin'} icon="arrow" testid="analyze-with-workpilot" disabled={!question.trim()}>
          Analyze with WorkPilot
        </Button>
      </div>
      <Uploads {...props} question={question} scenarioId={scenarioId} />
      <div className="features">
        <div><Icon name="doc" size={26} /><span>Searches your enterprise documents with Vertex AI Search</span></div>
        <div><Icon name="spark" size={26} /><span>Evidence-backed analysis with Gemini, run by an ADK agent</span></div>
        <div><Icon name="memory" size={26} /><span>Creates a decision record and Action Contracts</span></div>
        <div><Icon name="shield" size={26} /><span>A person approves, runs and confirms every action</span></div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// 2 · Knowledge
// --------------------------------------------------------------------------
export function KnowledgeStep(props: StepProps) {
  const { s, cfg, go, startAnalysis, analysis, run, busy } = props
  const [stale, setStale] = useState(false)
  if (!s?.retrieved) return <Empty title="No search yet">Ask a question on Start first.</Empty>
  const r = s.retrieved
  const loaded = s.sources.filter((x) => x.registered).length
  const cited = s.sources.filter((x) => x.cited).length
  const counts: Record<string, number> = {}
  for (const e of r.evidence) counts[e.source] = (counts[e.source] || 0) + 1
  return (
    <>
      <div className="panel-head">
        <div>
          <h2>{cfg.scenario_title}</h2>
          <p className="sub">Gathering relevant information from your enterprise sources.</p>
        </div>
        <Badge tone="done"><Icon name="check" size={13} /> {loaded} of {s.sources.length} sources loaded</Badge>
      </div>
      <p className="meta" data-testid="retrieval-mode">
        <Icon name="search" size={14} /> {r.mode} returned {r.evidence.length - (r.upload_passages || 0)} passages
        {r.upload_passages ? <> plus {r.upload_passages} from your uploads (searched in this session)</> : null} from {cited} of {s.sources.length} sources for: <i>“{r.question}”</i>
      </p>
      {r.error ? <div className="alert warn">Search fell back: {r.error}</div> : null}
      {!r.evidence.length ? (
        <div className="alert warn" data-testid="no-answer">
          <b>Nothing in the workspace answers this question.</b> {r.not_found} No analysis will run on no evidence. Try one of the sample scenarios on Start.
        </div>
      ) : r.not_found ? <div className="alert warn">{r.not_found}</div> : null}
      <div className="source-grid" data-testid="source-cards">
        {s.sources.map((src) => (
          <div key={src.name} className={`source-card${src.cited ? ' cited' : ''}`} data-testid="source-card">
            <div className={`file-icon t-${src.type.toLowerCase()}`}><Icon name="doc" size={22} /></div>
            <div className="source-body">
              <b>{src.title}</b>
              <span className="meta">{src.type} · {src.words.toLocaleString()} words · {fmtBytes(src.bytes)}{src.chunks ? ` · ${src.chunks} chunks` : ''}</span>
              {src.as_of ? <span className="meta">Dated {src.as_of}</span> : null}
              {src.uploaded ? <span className="meta">Uploaded by you · read by {src.parser || 'local parser'}</span> : null}
              <div className="source-foot">
                <code className="tag">{src.name}</code>
                {src.cited ? <Badge tone="done">Cited · {counts[src.name] || 0}</Badge> : src.registered ? <Badge tone="progress">{src.uploaded ? 'Uploaded' : 'Loaded'}</Badge> : <Badge>Not indexed</Badge>}
              </div>
            </div>
          </div>
        ))}
      </div>
      {!s.analysis ? (
        <>
          <Uploads {...props} onAdded={() => setStale(true)} />
          {stale ? (
            <div className="center-row left">
              <Button kind="secondary" icon="search" busy={busy === 'search'} testid="search-again"
                onClick={() => run('search', async () => { const out = await api.retrieve(s.session_id, r.question); setStale(false); return out })}>
                Search again with your documents
              </Button>
            </div>
          ) : null}
        </>
      ) : null}
      <details className="evidence-list">
        <summary>Show the {r.evidence.length} retrieved passages</summary>
        {r.evidence.map((e) => (
          <div key={`${e.source}-${e.rank}`} className="evidence">
            <div className="meta"><b>{e.rank}. {e.source}</b> · {e.locator} · score {e.score}</div>
            <p>{e.excerpt}</p>
          </div>
        ))}
      </details>
      <StepNav prev="start" go={go} next={
        s.analysis
          ? { label: 'Next: Analyze', to: 'analyze', testid: 'next-analyze' }
          : { label: 'Next: Analyze', onClick: startAnalysis, busy: analysis.running, disabled: !r.evidence.length, testid: 'next-analyze' }
      } />
    </>
  )
}

// --------------------------------------------------------------------------
// 3 · Analyze
// --------------------------------------------------------------------------
const STAGES = [
  // Until the first tool call: the agent's first Gemini turn deciding what to search.
  { id: 'start', label: 'Agent planning the turn' },
  { id: 'retrieving', label: 'Searching enterprise sources' },
  { id: 'reasoning', label: 'Analysing evidence with Gemini' },
  { id: 'structuring', label: 'Recording the decision' },
]

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(t)
  }, [active])
  return now
}

function Progress({ run }: { run: StepProps['analysis'] }) {
  const now = useNow(run.running)
  const at = (id: string) => run.events.find((e) => (id === 'start' ? e.type === 'start' : e.type === 'stage' && e.stage === id))?.at_ms
  const end = run.events.find((e) => e.type === 'done' || e.type === 'error')?.at_ms
  const elapsed = now - run.startedAt
  const rows = STAGES.map((st, i) => {
    const start = at(st.id)
    const later = STAGES.slice(i + 1).map((x) => at(x.id)).find((x) => x !== undefined)
    const stop = later ?? end
    return { ...st, start, stop }
  })
  return (
    <ul className="checklist" data-testid="analysis-progress">
      {rows.map((row) => {
        const state = row.start === undefined ? (end !== undefined ? 'skipped' : 'waiting') : row.stop !== undefined ? 'done' : 'active'
        const ms = row.start === undefined ? 0 : (row.stop ?? elapsed) - row.start
        return (
          <li key={row.id} className={state}>
            <span className="tick">{state === 'done' ? <Icon name="check" size={14} /> : state === 'active' ? <span className="spinner dark" /> : null}</span>
            <span>{row.label}</span>
            <span className="time">{state === 'waiting' ? '' : state === 'skipped' ? 'not reported' : seconds(ms)}</span>
          </li>
        )
      })}
      <li className="total"><span /><span>{run.running ? 'Elapsed' : 'Total, measured on the server'}</span><span className="time">{seconds(end ?? elapsed)}</span></li>
    </ul>
  )
}

export function BlockerCard({ b }: { b: Blocker }) {
  const initials = b.owner && !/unassigned|tbd/i.test(b.owner) ? b.owner.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() : ''
  return (
    <div className={`blocker p-${(b.priority || 'medium').toLowerCase()}`} data-testid="blocker-card">
      <div className="blocker-head"><b>{b.blocker}</b>{b.priority ? <Badge>{b.priority}</Badge> : null}</div>
      {b.business_impact ? <p>{b.business_impact}</p> : null}
      <div className="kv">
        <div><span>Owner</span><span className="who"><Avatar name={b.owner || 'Unassigned'} initials={initials} /> {b.owner || 'Unassigned'}</span></div>
        <div><span>Due</span><span><Icon name="calendar" size={14} /> {b.due_date || 'TBD'}</span></div>
      </div>
      {b.evidence.length ? <div className="ev-chips">{b.evidence.map((e) => <code key={e} className="tag">{e}</code>)}</div> : null}
    </div>
  )
}

export function VerdictBanner({ s, initial }: { s: Session; initial?: boolean }) {
  const a = s.analysis!
  const v = (initial ? a.initial_verdict : a.verdict) || a.initial_verdict
  if (!v) return null
  return (
    <div className={`verdict tone-${v.tone}`} data-testid="verdict">
      <div className="verdict-main">
        <span className="verdict-icon"><Icon name={v.tone === 'green' ? 'check' : 'alert'} size={26} /></span>
        <div>
          <span className="eyebrow">{initial ? 'Initial assessment' : 'Current readiness'}</span>
          <b className="verdict-status" data-testid="verdict-status">{v.status === 'NOT READY' ? 'NOT READY FOR GO-LIVE' : v.status}</b>
          <span className="meta">{openLine(v)}</span>
        </div>
      </div>
      <div className="stats">
        <div><b>{a.stats.blockers}</b><span>Blockers</span></div>
        <div><b>{a.stats.owners}</b><span>Owners</span></div>
        <div><b>{a.stats.conditions}</b><span>Approval Conditions</span></div>
      </div>
    </div>
  )
}

export function AnalyzeStep({ s, go, analysis, startAnalysis }: StepProps) {
  const [tab, setTab] = useState<'findings' | 'evidence'>('findings')
  const a = s?.analysis
  const showProgress = analysis.running || analysis.events.length > 0
  return (
    <>
      <div className="panel-head">
        <div>
          <h2>{analysis.running ? 'Analysis in Progress' : a ? 'Evidence and Key Findings' : 'Analysis'}</h2>
          <p className="sub">WorkPilot searches your documents, analyses the evidence and identifies blockers.</p>
        </div>
        {a ? <ProducedBy mode={a.ai_mode} /> : null}
      </div>
      {showProgress ? <Progress run={analysis} /> : null}
      {analysis.error ? <div className="alert error" role="alert">Analysis failed: {analysis.error}</div> : null}
      {!a && !analysis.running ? (
        <div className="center-row"><Button onClick={startAnalysis} icon="arrow" testid="run-analysis">{analysis.error ? 'Try again' : 'Run analysis'}</Button></div>
      ) : null}
      {a && !a.grounded ? (
        <div className="alert warn" data-testid="no-answer">
          <b>Nothing in the workspace answers this question.</b> {a.no_answer} No decision was recorded. Try one of the sample scenarios on Start.
        </div>
      ) : null}
      {a && a.grounded && !analysis.running ? (
        <>
          {!showProgress && a.duration_ms ? <p className="meta">Analysis took {seconds(a.duration_ms)} · {a.retrieval_mode}</p> : null}
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'findings'} onClick={() => setTab('findings')}>Key Findings</button>
            <button role="tab" aria-selected={tab === 'evidence'} onClick={() => setTab('evidence')}>Evidence ({s?.retrieved?.evidence.length ?? 0})</button>
          </div>
          {tab === 'findings' ? (
            <>
              <VerdictBanner s={s!} initial />
              {a.sections.finding ? <p className="finding" data-testid="finding">{a.sections.finding}</p> : null}
              <div className="blocker-grid">{a.sections.blockers.map((b, i) => <BlockerCard key={i} b={b} />)}</div>
            </>
          ) : (
            <div className="evidence-list open">
              {(s?.retrieved?.evidence || []).map((e) => (
                <div key={`${e.source}-${e.rank}`} className="evidence">
                  <div className="meta"><b>{e.rank}. {e.source}</b> · {e.locator} · score {e.score}</div>
                  <p>{e.excerpt}</p>
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}
      <StepNav prev="knowledge" go={go} next={a?.grounded && !analysis.running ? { label: 'Next: Decision', to: 'decision', testid: 'next-decision' } : undefined} />
    </>
  )
}
