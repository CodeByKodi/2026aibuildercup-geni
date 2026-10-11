import type { ReactNode } from 'react'

const PATHS: Record<string, ReactNode> = {
  plus: <path d="M12 5v14M5 12h14" />,
  sessions: <><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M8 9h8M8 13h8M8 17h5" /></>,
  memory: <><path d="M5 4h11l3 3v13H5z" /><path d="M9 4v5h6V4M9 14h6M9 17h4" /></>,
  status: <><circle cx="12" cy="12" r="8" /><path d="M12 8v4l3 2" /></>,
  doc: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></>,
  check: <path d="M5 12.5l4.2 4.2L19 7" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  back: <path d="M19 12H5M11 6l-6 6 6 6" />,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M16 16l5 5" /></>,
  spark: <path d="M12 3l2 6.5L20.5 12 14 14l-2 7-2-7-6.5-2L10 9.5z" />,
  shield: <><path d="M12 3l7 3v6c0 4.5-3 7.4-7 9-4-1.6-7-4.5-7-9V6z" /><path d="M9.2 12.1l1.9 1.9 3.8-3.9" /></>,
  bolt: <path d="M13 3L5 14h6l-1 7 9-12h-6z" />,
  chart: <path d="M4 19h16M7 16V9M12 16V6M17 16v-4" />,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>,
  calendar: <><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M4 10h16M9 3v4M15 3v4" /></>,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5v.5" /></>,
  pause: <><circle cx="12" cy="12" r="9" /><path d="M10 9v6M14 9v6" /></>,
  play: <><circle cx="12" cy="12" r="9" /><path d="M10 8.5l5 3.5-5 3.5z" /></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></>,
  link: <><path d="M9.6 14.2l-1.1 1.1a3.1 3.1 0 11-4.4-4.4l3.2-3.2a3.1 3.1 0 014.4 0" /><path d="M14.4 9.8l1.1-1.1a3.1 3.1 0 114.4 4.4l-3.2 3.2a3.1 3.1 0 01-4.4 0" /></>,
}

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name] ?? PATHS.doc}
    </svg>
  )
}

export function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 3l3 9.6L28.6 16 19 19.4 16 29l-3-9.6L3.4 16 13 12.6z" fill="#60a5fa" />
      <path d="M16 9l1.6 5.4L23 16l-5.4 1.6L16 23l-1.6-5.4L9 16l5.4-1.6z" fill="#f97316" />
    </svg>
  )
}

const TONES: Record<string, string> = {
  CRITICAL: 'urgent', HIGH: 'urgent', MEDIUM: 'warn', LOW: 'muted',
  PENDING: 'proposed', APPROVED: 'progress', REJECTED: 'warn', EXECUTED: 'done', 'NOT STARTED': 'muted', FAILED: 'warn',
  'NOT READY': 'urgent', 'AT RISK': 'warn', 'READY FOR APPROVAL': 'done',
  ACHIEVED: 'done', 'PARTIALLY ACHIEVED': 'warn', 'NOT ACHIEVED': 'urgent', BLOCKED: 'urgent',
}

export function Badge({ children, tone }: { children: ReactNode; tone?: string }) {
  const key = String(children ?? '').toUpperCase().replace(/_/g, ' ')
  return <span className={`badge ${tone ?? TONES[key] ?? 'muted'}`}>{children}</span>
}

export function Avatar({ name, initials }: { name: string; initials: string }) {
  if (!initials) return <span className="avatar empty" title={name}>?</span>
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360
  return <span className="avatar" title={name} style={{ background: `hsl(${h} 70% 92%)`, color: `hsl(${h} 55% 32%)` }}>{initials}</span>
}

export function Button({ children, onClick, kind = 'primary', disabled, icon, busy, testid, title }: {
  children: ReactNode; onClick?: () => void; kind?: 'primary' | 'secondary' | 'ghost' | 'danger'; disabled?: boolean
  icon?: string; busy?: boolean; testid?: string; title?: string
}) {
  return (
    <button type="button" className={`btn ${kind}`} onClick={onClick} disabled={disabled || busy} data-testid={testid} title={title} aria-busy={busy || undefined}>
      {busy ? <span className="spinner" aria-hidden="true" /> : null}
      {children}
      {icon && !busy ? <Icon name={icon} size={16} /> : null}
    </button>
  )
}

export function fmtDate(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function fmtDateTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  return `${(n / 1024).toFixed(1)} KB`
}

export function seconds(ms: number): string {
  return ms < 10000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 1000)} s`
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-card">
      <b>{title}</b>
      {children ? <p>{children}</p> : null}
    </div>
  )
}
