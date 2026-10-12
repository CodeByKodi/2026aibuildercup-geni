// Typed client for workpilot/api.py. Every value shown in the UI comes from these responses.

export type Tone = 'proposed' | 'progress' | 'done' | 'warn' | 'urgent' | 'muted' | string

export interface SourceCard {
  name: string; title: string; type: string; bytes: number; words: number; chunks: number
  parser: string; registered: boolean; as_of: string; cited: boolean
  uploaded?: boolean; source_id?: string; truncated?: boolean; uploaded_at?: string
}
export interface Scenario { id: string; label: string; persona: string; question: string; hint: string }
export interface Config {
  default_question: string; persona: string; scenario_title: string; release: string; target_go_live: string
  scenarios: Scenario[]; sources: SourceCard[]; outcome_statuses: string[]; followups_connected: boolean; challenge: string
}
export interface Blocker {
  blocker: string; business_impact: string; recommendation: string; owner: string; due_date: string; priority: string; evidence: string[]
}
export interface Sections {
  finding: string; recommendation: string; rationale: string; expected_outcome: string; missing_information: string
  open_questions: string[]; readiness_status: string; approval_conditions: string[]; blockers: Blocker[]
}
export interface Verdict { status: string; tone: string; open: number; total: number; headline: string }
export interface Analysis {
  grounded: boolean; decision_id: string; created_at: string; ai_mode: string; retrieval_mode: string; duration_ms: number
  sections: Sections; stats: { blockers: number; urgent: number; owners: number; conditions: number }
  initial_verdict: Verdict | null; verdict: Verdict | null
  evidence_map: { source: string; title: string; supports: string[] }[]; no_answer: string
}
export interface Receipt { kind: string; reference: string; target: string; at: string }
export interface Contract {
  action_id: string; title: string; owner: string; initials: string; due_date: string; due_label: string; priority: string
  status: string; approval: string; execution: string; expected_outcome: string; rationale: string; evidence: string[]; evidence_sources: string[]
  approved_by: string; approved_at: string; outcome_status: string; actual_outcome: string; receipt: Receipt | null
}
export interface Evidence { source: string; excerpt: string; locator: string; section: string; page: string; score: string; rank: string }
export interface Progress { total: number; pending: number; approved: number; rejected: number; executed: number; measurable: number; measured: number }
export interface OutcomeRow { title: string; owner: string; execution: string; expected: string; actual: string; outcome: string; tone: string; state: string }
export interface Impact {
  baseline_minutes: number; workpilot_seconds: number; workpilot_steps: string[]; net_minutes: number
  hours: number | null; hours_label: string; tiles: { value: string; label: string; tone: string }[]
  metrics: Record<string, number>; assumptions: Record<string, unknown>; breakdown_minutes: Record<string, number>
  per_task: Record<string, unknown>[]; methodology: string; computed_at: string
}
export interface StepMark { at: string; ms?: number; trace?: string; mode?: string; passages?: number; ran?: number; fallback?: boolean }
export interface ProofRow { service: string; resource: string; used_for: string; last: StepMark | null; detail: string; count?: number | null }
export interface Proof { rows: ProofRow[]; ids: { session: string; decision: string; actions: string[] }; events: number; executed: number; unapproved_executions: number }
export interface Preview {
  gmail: { to: string; subject: string; body: string; sent: boolean }
  calendar: { title: string; date: string; attendee: string; description: string }
  connected: boolean; approval: string
}
export interface AuditEvent { event_id: string; event_type: string; action_id: string; at: string; by: string; detail: string; outcome_status: string }
export interface Audit { events: AuditEvent[]; store: string; bigquery: { configured: boolean; table: string } }
export interface AgentMessage { ok: boolean; fallback?: boolean; text: string; readiness?: string; headline?: string; impact_hours?: number; steps?: string[]; at: string }
export interface Session {
  session_id: string; created_at: string; question: string; scenario_id: string; persona: string; stage: string
  flags: Record<string, boolean>
  agent: { stage: string; mode: 'working' | 'paused' | 'ready'; text: string; can_resume: boolean }
  agent_message: AgentMessage | null; agent_message_current: boolean; agent_trace: string[]
  retrieved: { question: string; mode: string; error: string; not_found: string; evidence: Evidence[]; upload_passages: number } | null
  sources: SourceCard[]; analysis: Analysis | null; contracts: Contract[]; contracts_mode: string
  progress: Progress; tally: { label: string; count: number; tone: string }[]; outcomes: OutcomeRow[]
  readiness: { status: string; tone: string; title: string; text: string } | null
  followups_connected: boolean; bigquery_events: number | null
  message?: string
}
export interface StreamEvent { type: 'start' | 'stage' | 'tick' | 'done' | 'error'; at_ms: number; stage?: string; label?: string; session?: Session; detail?: string }
export interface Suggestion { suggested_status: string; explanation: string; mode: string; agent_trace: string[] }
export interface SessionSummary { session_id: string; question: string; stage: string; readiness?: string; decision_id: string; contracts: number; created_at: string; updated_at: string }
export interface MemoryEntry {
  decision_id: string; question: string; finding: string; recommendation: string; rationale: string; status: string
  readiness_status: string; expected_outcome: string; actual_outcome: string; approved_by: string; created_at: string
  evidence: { source: string; excerpt: string }[]; actions: Contract[]
}
export interface StatusRow { technology: string; status: string; detail: string; use: string }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not JSON */ }
    throw new ApiError(res.status, String(detail))
  }
  return res.json() as Promise<T>
}
const post = <T,>(path: string, body: unknown = {}) => call<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  config: () => call<Config>('/api/config'),
  create: (question?: string, scenario_id?: string) => post<Session>('/api/sessions', { question, scenario_id }),
  get: (sid: string) => call<Session>(`/api/sessions/${sid}`),
  retrieve: (sid: string, question?: string, scenario_id?: string) => post<Session>(`/api/sessions/${sid}/retrieve`, { question, scenario_id }),
  contracts: (sid: string) => post<Session>(`/api/sessions/${sid}/contracts`),
  approve: (sid: string, aid: string) => post<Session>(`/api/sessions/${sid}/actions/${aid}/approve`),
  reject: (sid: string, aid: string) => post<Session>(`/api/sessions/${sid}/actions/${aid}/reject`),
  fields: (sid: string, aid: string, owner: string, due_date: string) => post<Session>(`/api/sessions/${sid}/actions/${aid}/fields`, { owner, due_date }),
  execute: (sid: string, aid: string, type: 'gmail_draft' | 'calendar_followup') => post<Session>(`/api/sessions/${sid}/actions/${aid}/execute`, { type }),
  suggest: (sid: string, aid: string, actual_outcome: string) => post<Suggestion>(`/api/sessions/${sid}/actions/${aid}/outcome/suggest`, { actual_outcome }),
  confirm: (sid: string, aid: string, actual_outcome: string, outcome_status: string) =>
    post<Session>(`/api/sessions/${sid}/actions/${aid}/outcome/confirm`, { actual_outcome, outcome_status }),
  resume: (sid: string) => post<Session>(`/api/sessions/${sid}/resume`),
  async upload(sid: string, file: File): Promise<Session> {
    const res = await fetch(`/api/sessions/${sid}/documents?name=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
    })
    if (!res.ok) {
      let detail = res.statusText
      try { detail = (await res.json()).detail ?? detail } catch { /* not JSON */ }
      throw new ApiError(res.status, `${file.name}: ${detail}`)
    }
    return res.json() as Promise<Session>
  },
  removeUpload: (sid: string, sourceId: string) => call<Session>(`/api/sessions/${sid}/documents/${sourceId}`, { method: 'DELETE' }),
  executeApproved: (sid: string, type: 'gmail_draft' | 'calendar_followup') => post<Session>(`/api/sessions/${sid}/execute-approved`, { type }),
  audit: (sid: string) => call<Audit>(`/api/sessions/${sid}/audit`),
  proof: (sid: string) => call<Proof>(`/api/sessions/${sid}/proof`),
  preview: (sid: string, aid: string) => call<Preview>(`/api/sessions/${sid}/actions/${aid}/preview`),
  impact: (sid: string) => call<{ impact: Impact | null }>(`/api/sessions/${sid}/impact`),
  sessions: (ids: string[]) => call<SessionSummary[]>(`/api/sessions?ids=${encodeURIComponent(ids.join(','))}`),
  memory: (ids: string[]) => call<MemoryEntry[]>(`/api/memory?ids=${encodeURIComponent(ids.join(','))}`),
  status: () => call<{ rows: StatusRow[]; checked_at: string }>('/api/status'),

  /** Decide: reads the NDJSON stream of the agent's stages until done or error. */
  async analyze(sid: string, onEvent: (e: StreamEvent) => void): Promise<Session> {
    const res = await fetch(`/api/sessions/${sid}/analyze`, { method: 'POST' })
    if (!res.ok || !res.body) throw new ApiError(res.status, `Analysis could not start (${res.status})`)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let nl: number
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl).trim()
        buffer = buffer.slice(nl + 1)
        if (!line) continue
        const event = JSON.parse(line) as StreamEvent
        onEvent(event)
        if (event.type === 'done' && event.session) return event.session
        if (event.type === 'error') throw new ApiError(502, event.detail || 'Analysis failed')
      }
    }
    throw new ApiError(502, 'The analysis stream ended early')
  },
}

// Sessions this browser started: a per-viewer convenience, not shared state.
const KEY = 'workpilot.sessions'
export function rememberedSessions(): string[] {
  try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] }
}
export function rememberSession(sid: string) {
  try {
    const ids = [sid, ...rememberedSessions().filter((s) => s !== sid)].slice(0, 20)
    localStorage.setItem(KEY, JSON.stringify(ids))
  } catch { /* storage unavailable */ }
}
