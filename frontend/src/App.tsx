import { useCallback, useEffect, useRef, useState } from 'react'
import { api, rememberSession, type Config, type Session, type StreamEvent } from './api'
import { Icon, Logo } from './ui'
import { StartStep, KnowledgeStep, AnalyzeStep } from './steps/early'
import { DecisionStep, ActionsStep } from './steps/decide'
import { ApprovalStep } from './steps/approval'
import { OutcomeStep } from './steps/outcome'
import { SessionsPage, MemoryPage, StatusPage } from './pages'
import { EvidenceDrawer, ProofStrip } from './proof'

export const STEPS = [
  { id: 'start', label: 'Start' },
  { id: 'knowledge', label: 'Knowledge' },
  { id: 'analyze', label: 'Analyze' },
  { id: 'decision', label: 'Decision' },
  { id: 'actions', label: 'Actions' },
  { id: 'approval', label: 'Approval' },
  { id: 'outcome', label: 'Outcome & Impact' },
] as const
export type StepId = (typeof STEPS)[number]['id']
type Page = 'workflow' | 'sessions' | 'memory' | 'status'

export interface RunState { running: boolean; events: StreamEvent[]; startedAt: number; error: string }

export interface StepProps {
  s: Session | null
  cfg: Config
  busy: string
  run: (key: string, fn: () => Promise<Session | void>) => Promise<void>
  go: (step: StepId) => void
  analysis: RunState
  startAnalysis: () => void
  begin: (question: string, scenarioId?: string) => Promise<void>
  /** The open session, or a new one when there is none or it already has a decision. */
  ensureSession: (question?: string, scenarioId?: string) => Promise<Session>
  /** Open the evidence behind one cited source. */
  cite: (source: string) => void
}

const grounded = (s: Session | null) => !!s?.analysis?.grounded

/** A step is done only when its work is recorded; opening it never counts. */
export function stepDone(id: StepId, s: Session | null): boolean {
  if (!s) return false
  const p = s.progress
  switch (id) {
    case 'start': return !!s.retrieved
    case 'knowledge': return !!s.retrieved?.evidence.length
    case 'analyze': return grounded(s)
    case 'decision':
    case 'actions': return s.contracts.length > 0
    case 'approval': return p.total > 0 && p.pending === 0
    case 'outcome': return p.measurable > 0 && p.measured === p.measurable
  }
}

export function stepOpen(id: StepId, s: Session | null): boolean {
  switch (id) {
    case 'start': return true
    case 'knowledge':
    case 'analyze': return !!s?.retrieved
    case 'decision':
    case 'actions': return grounded(s)
    case 'approval':
    case 'outcome': return !!s && s.contracts.length > 0
  }
}

/** Where a restored session should open: the first step whose work is still to do. */
function resumeStep(s: Session | null): StepId {
  if (!s?.retrieved) return 'start'
  if (!s.analysis) return 'knowledge'
  if (!s.analysis.grounded) return 'analyze'
  if (!s.contracts.length) return 'decision'
  const p = s.progress
  if (p.pending > 0 || p.executed < p.approved) return 'approval'
  return 'outcome'
}

function readUrl() {
  const q = new URLSearchParams(window.location.search)
  return { sid: q.get('s') || '', step: (q.get('step') || '') as StepId, page: (q.get('page') || 'workflow') as Page }
}

function writeUrl(sid: string, step: StepId, page: Page) {
  const q = new URLSearchParams()
  if (sid) q.set('s', sid)
  if (page !== 'workflow') q.set('page', page)
  else if (sid) q.set('step', step)
  const url = `${window.location.pathname}${q.toString() ? `?${q}` : ''}`
  if (url !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, '', url)
}

const IDLE: RunState = { running: false, events: [], startedAt: 0, error: '' }

export default function App() {
  const [cfg, setCfg] = useState<Config | null>(null)
  const [s, setS] = useState<Session | null>(null)
  const [step, setStep] = useState<StepId>('start')
  const [page, setPage] = useState<Page>('workflow')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [analysis, setAnalysis] = useState<RunState>(IDLE)
  const [navOpen, setNavOpen] = useState(false)
  const [citation, setCitation] = useState('')

  useEffect(() => {
    const { sid, step: wanted, page: p } = readUrl()
    setPage(['sessions', 'memory', 'status'].includes(p) ? p : 'workflow')
    Promise.all([api.config(), sid ? api.get(sid).catch(() => null) : Promise.resolve(null)])
      .then(([c, sess]) => {
        setCfg(c)
        if (sess) {
          setS(sess)
          rememberSession(sess.session_id)
          setStep(STEPS.some((x) => x.id === wanted) && stepOpen(wanted, sess) ? wanted : resumeStep(sess))
        } else if (sid) {
          setError(`Session ${sid} was not found. Start a new analysis.`)
        }
      })
      .catch((e) => setError(`WorkPilot could not load: ${e.message}`))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { writeUrl(s?.session_id || '', step, page) }, [s?.session_id, step, page])
  useEffect(() => { window.scrollTo({ top: 0 }) }, [step, page])

  const accept = useCallback((next: Session) => {
    setS(next)
    rememberSession(next.session_id)
  }, [])

  const run = useCallback(async (key: string, fn: () => Promise<Session | void>) => {
    setBusy(key)
    setError('')
    try {
      const out = await fn()
      if (out) accept(out)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }, [accept])

  const go = useCallback((target: StepId) => { setPage('workflow'); setStep(target); setNavOpen(false) }, [])

  const ensureSession = useCallback(async (question?: string, scenarioId?: string) => {
    if (s && !s.analysis) return s
    const created = await api.create(question, scenarioId)
    accept(created)
    return created
  }, [s, accept])

  const begin = useCallback(async (question: string, scenarioId?: string) => {
    await run('begin', async () => {
      const sess = await ensureSession(question, scenarioId)
      const out = await api.retrieve(sess.session_id, question, scenarioId)
      setAnalysis(IDLE)
      setStep('knowledge')
      return out
    })
  }, [run, ensureSession])

  const startAnalysis = useCallback(() => {
    if (!s || analysis.running) return
    const sid = s.session_id
    setError('')
    setAnalysis({ running: true, events: [], startedAt: Date.now(), error: '' })
    setStep('analyze')
    api.analyze(sid, (e) => { if (e.type !== 'tick') setAnalysis((r) => ({ ...r, events: [...r.events, e] })) })
      .then((out) => { accept(out); setAnalysis((r) => ({ ...r, running: false })) })
      .catch((e) => setAnalysis((r) => ({ ...r, running: false, error: (e as Error).message })))
  }, [s, analysis.running, accept])

  const newAnalysis = () => {
    setS(null)
    setAnalysis(IDLE)
    setError('')
    go('start')
  }

  const openPage = (p: Page) => { setPage(p); setNavOpen(false) }

  if (loading || !cfg) {
    return <div className="boot">{error ? <p className="error">{error}</p> : <><span className="spinner dark" /> Loading WorkPilot…</>}</div>
  }

  const props: StepProps = { s, cfg, busy, run, go, analysis, startAnalysis, begin, ensureSession, cite: setCitation }
  const showStrip = ['start', 'analyze', 'decision', 'actions', 'outcome'].includes(step)
  const persona = cfg.persona.match(/You are ([^,]+), ([^.]+)/)
  const personaName = persona?.[1] ?? 'Reviewer'
  const personaRole = persona?.[2] ?? ''

  return (
    <div className={`shell${navOpen ? ' nav-open' : ''}`}>
      <aside className="sidebar" aria-label="WorkPilot navigation">
        <div className="brand">
          <Logo />
          <div><b>WorkPilot AI</b><span>From Knowledge to Outcomes</span></div>
        </div>
        <nav>
          <button className={page === 'workflow' ? 'active' : ''} onClick={() => (page === 'workflow' && s ? newAnalysis() : openPage('workflow'))} data-testid="nav-new">
            <Icon name="plus" /> {page === 'workflow' && s ? 'New Analysis' : s ? 'Current Analysis' : 'New Analysis'}
          </button>
          <button className={page === 'sessions' ? 'active' : ''} onClick={() => openPage('sessions')} data-testid="nav-sessions"><Icon name="sessions" /> My Sessions</button>
          <button className={page === 'memory' ? 'active' : ''} onClick={() => openPage('memory')} data-testid="nav-memory"><Icon name="memory" /> Decision Memory</button>
          <button className={page === 'status' ? 'active' : ''} onClick={() => openPage('status')} data-testid="nav-status"><Icon name="status" /> System Status</button>
        </nav>
        <div className="persona" title="The reviewer named in the sample scenario">
          <span className="avatar dark">{personaName.split(' ').map((w) => w[0]).join('').slice(0, 2)}</span>
          <div><b>{personaName}</b><span>{personaRole}</span></div>
        </div>
      </aside>

      <header className="topbar">
        <button className="icon-btn" onClick={() => setNavOpen((o) => !o)} aria-label="Open navigation"><Icon name="menu" /></button>
        <Logo /><b>WorkPilot AI</b>
      </header>
      {navOpen ? <div className="scrim" onClick={() => setNavOpen(false)} /> : null}

      <main className="main">
        {page === 'workflow' ? (
          <>
            <Stepper s={s} step={step} go={go} />
            {showStrip ? <ProofStrip s={s} cfg={cfg} /> : null}
            {error ? <div className="alert error" role="alert" data-testid="error">{error}</div> : null}
            <section className="panel" data-testid={`step-${step}`}>
              {step === 'start' && <StartStep {...props} />}
              {step === 'knowledge' && <KnowledgeStep {...props} />}
              {step === 'analyze' && <AnalyzeStep {...props} />}
              {step === 'decision' && <DecisionStep {...props} />}
              {step === 'actions' && <ActionsStep {...props} />}
              {step === 'approval' && <ApprovalStep {...props} />}
              {step === 'outcome' && <OutcomeStep {...props} />}
            </section>
          </>
        ) : (
          <section className="panel" data-testid={`page-${page}`}>
            {page === 'sessions' && <SessionsPage current={s?.session_id || ''} />}
            {page === 'memory' && <MemoryPage />}
            {page === 'status' && <StatusPage />}
          </section>
        )}
        <footer className="foot">
          {cfg.challenge} · Cloud Run · Firestore · Vertex AI Search · Gemini via ADK
          {s ? <> · Session <code data-testid="session-id">{s.session_id}</code></> : null}
        </footer>
      </main>
      {citation && s ? <EvidenceDrawer s={s} source={citation} onClose={() => setCitation('')} /> : null}
    </div>
  )
}

function Stepper({ s, step, go }: { s: Session | null; step: StepId; go: (id: StepId) => void }) {
  const ref = useRef<HTMLOListElement>(null)
  useEffect(() => {
    const list = ref.current
    const current = list?.querySelector<HTMLElement>('.current')
    if (list && current) list.scrollLeft = current.offsetLeft - list.clientWidth / 2 + current.clientWidth / 2
  }, [step])
  return (
    <ol className="stepper" aria-label="Workflow steps" ref={ref}>
      {STEPS.map((x, i) => {
        const done = stepDone(x.id, s)
        const open = stepOpen(x.id, s)
        const cls = [x.id === step ? 'current' : '', done ? 'done' : '', open ? '' : 'locked'].join(' ').trim()
        return (
          <li key={x.id} className={cls}>
            <button onClick={() => open && go(x.id)} disabled={!open} aria-current={x.id === step ? 'step' : undefined} data-testid={`stepper-${x.id}`}>
              <span className="dot">{done && x.id !== step ? <Icon name="check" size={13} /> : i + 1}</span>
              <span className="lbl">{x.label}</span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

export function StepNav({ prev, next, go, children }: { prev?: StepId; next?: { label: string; to?: StepId; onClick?: () => void; disabled?: boolean; busy?: boolean; testid?: string; title?: string }; go: (id: StepId) => void; children?: React.ReactNode }) {
  return (
    <div className="step-nav">
      {prev ? <button className="btn secondary" onClick={() => go(prev)}><Icon name="back" size={16} /> Previous</button> : <span />}
      <div className="step-nav-right">
        {children}
        {next ? (
          <button className="btn primary" disabled={next.disabled || next.busy} onClick={() => (next.onClick ? next.onClick() : next.to && go(next.to))} data-testid={next.testid} title={next.title}>
            {next.busy ? <span className="spinner" /> : null}{next.label} {next.busy ? null : <Icon name="arrow" size={16} />}
          </button>
        ) : null}
      </div>
    </div>
  )
}
