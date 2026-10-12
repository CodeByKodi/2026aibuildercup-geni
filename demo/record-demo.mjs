// WorkPilot AI · 3-minute submission demo, recorded from the live deployment.
//
// Playwright drives the real workflow (nothing is mocked); Chrome's screencast captures the frames;
// ffmpeg assembles them. Long AI waits are sped up in the edit, and an on-screen badge counts the
// real elapsed seconds while they play, so the video never hides how long a step took.
// Every number in the narration is read from the screen during the run.
//
//   node demo/record-demo.mjs --out /path/to/folder [--url https://…] [--no-voice]
//
// Needs: npm ci (Playwright + Chromium), ffmpeg; macOS `say` for the optional synthetic voice.
// Side effects: one new session, and three Gmail drafts (never sent) in the deployment's demo mailbox.
import { createRequire } from 'module'
import { execFileSync } from 'child_process'
import fs from 'fs'
import path from 'path'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`)
  return i > -1 ? process.argv[i + 1] : fallback
}
const URL = arg('url', 'https://workpilot-geni-475836937080.asia-south1.run.app')
const OUT = path.resolve(arg('out', 'demo-out'))
const VOICE = !process.argv.includes('--no-voice')
const VOICE_NAME = arg('voice', 'Aman')
const RATE = arg('rate', '170')
// Narration tone: 'soft' lowers the pitch about a semitone at the same speed, tames sibilance and highs,
// and adds a faint room; 'plain' leaves the voice as macOS renders it. Timing is identical either way.
const TONE = arg('tone', 'soft')
const CLIP_FILTER = TONE === 'plain' ? 'aresample=48000'
  : 'aresample=48000,asetrate=48000*0.94,aresample=48000,atempo=1/0.94,deesser=i=0.5,highshelf=f=4500:g=-5,'
    + 'lowshelf=f=180:g=2,acompressor=threshold=0.125:ratio=2:attack=20:release=250'
const MIX_FILTER = TONE === 'plain' ? 'apad' : 'aecho=0.8:0.6:18:0.12,loudnorm=I=-18:TP=-2,apad'
const FRAMES = path.join(OUT, 'frames')
const CUES = path.join(OUT, 'voice')
fs.rmSync(OUT, { recursive: true, force: true })
fs.mkdirSync(FRAMES, { recursive: true })
fs.mkdirSync(CUES, { recursive: true })

// ---------------------------------------------------------------------------------------------
// Output clock. Each segment of wall time plays at a speed; frames and voice cues are placed on
// the output timeline by integrating over these segments.
// ---------------------------------------------------------------------------------------------
const now = () => Date.now() / 1000
const segments = [{ at: now(), speed: 1 }]
const setSpeed = (speed, at = now()) => segments.push({ at, speed })
function outTime(t) {
  let out = 0
  for (let i = 0; i < segments.length; i++) {
    const a = segments[i].at
    const b = i + 1 < segments.length ? segments[i + 1].at : Infinity
    if (t <= a) break
    out += (Math.min(t, b) - a) / segments[i].speed
  }
  return out
}
const CUT = 1000 // effectively removed from the video (voice synthesis pauses)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const cues = [] // { out, file, text }
const beats = [] // { out, title }
const facts = {}

let clips = 0
function synth(text) {
  const file = path.join(CUES, `cue-${String(++clips).padStart(2, '0')}.aiff`) // one file per sentence
  if (!VOICE) return { file: null, seconds: Math.max(1.6, text.split(/\s+/).length / 2.8) }
  execFileSync('say', ['-v', VOICE_NAME, '-r', RATE, '-o', file, text])
  const seconds = parseFloat(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file]).toString())
  return { file, seconds }
}

// ---------------------------------------------------------------------------------------------
// In-page overlay: caption bar, step chip, speed badge, cursor, title and end cards.
// ---------------------------------------------------------------------------------------------
const OVERLAY = () => {
  if (document.getElementById('wp-demo')) return
  const css = `
  #wp-demo{position:fixed;inset:0;pointer-events:none;z-index:2147483647;font-family:Inter,-apple-system,"Segoe UI",Roboto,sans-serif}
  #wp-cap{position:absolute;left:50%;bottom:26px;transform:translateX(-50%);max-width:1040px;background:rgba(10,18,40,.88);color:#fff;
    padding:12px 22px;border-radius:12px;font-size:21px;line-height:1.4;text-align:center;opacity:0;transition:opacity .25s;box-shadow:0 8px 30px rgba(0,0,0,.25)}
  #wp-chip{position:absolute;left:20px;top:300px;background:#1a56db;color:#fff;font-weight:700;font-size:14px;letter-spacing:.04em;
    padding:7px 12px;border-radius:999px;opacity:0;transition:opacity .25s;text-transform:uppercase}
  #wp-speed{position:absolute;right:24px;top:20px;background:#111827;color:#fff;font-size:16px;font-weight:600;padding:8px 14px;border-radius:10px;
    opacity:0;transition:opacity .2s}
  #wp-cursor{position:absolute;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;background:rgba(26,86,219,.35);
    border:2px solid #1a56db;left:-40px;top:-40px;transition:left .55s ease,top .55s ease,transform .15s}
  #wp-cursor.down{transform:scale(.7);background:rgba(26,86,219,.6)}
  #wp-card{position:absolute;inset:0;background:linear-gradient(135deg,#0b1330 0%,#14275e 60%,#1a56db 100%);color:#fff;display:flex;
    flex-direction:column;align-items:center;justify-content:center;text-align:center;opacity:0;transition:opacity .5s;padding:60px}
  #wp-card h1{font-size:64px;margin:0 0 10px;font-weight:800;letter-spacing:-.02em}
  #wp-card h2{font-size:28px;margin:0 0 34px;font-weight:500;opacity:.9}
  #wp-card .flow{font-size:24px;font-weight:600;margin:0 0 30px}
  #wp-card .flow b{color:#fbbf24}
  #wp-card p{font-size:19px;margin:6px 0;opacity:.85}
  #wp-card code{font-size:17px;background:rgba(255,255,255,.12);padding:4px 10px;border-radius:6px}`
  const style = document.createElement('style')
  style.textContent = css
  const root = document.createElement('div')
  root.id = 'wp-demo'
  root.innerHTML = '<div id="wp-card"></div><div id="wp-chip"></div><div id="wp-cap"></div><div id="wp-speed"></div><div id="wp-cursor"></div>'
  document.head.appendChild(style)
  document.body.appendChild(root)
}

async function ui(page, fn, argument) {
  await page.evaluate(OVERLAY)
  return page.evaluate(fn, argument)
}
const caption = (page, text) => ui(page, (t) => {
  const el = document.getElementById('wp-cap')
  if (t) el.textContent = t
  el.style.opacity = t ? '1' : '0'
}, text)
const chip = (page, text) => ui(page, (t) => {
  const el = document.getElementById('wp-chip')
  if (t) el.textContent = t
  el.style.opacity = t ? '1' : '0'
}, text)
const card = (page, html) => ui(page, (h) => {
  const el = document.getElementById('wp-card')
  if (h) el.innerHTML = h
  el.style.opacity = h ? '1' : '0'
}, html)

// Speak one or more sentences: the caption shows each sentence while its voice plays (real time).
async function say(page, sentences, { during } = {}) {
  const list = Array.isArray(sentences) ? sentences : [sentences]
  setSpeed(CUT) // voice synthesis happens off camera
  const voiced = list.map((text) => ({ text, ...synth(text) }))
  setSpeed(1)
  const work = during ? during() : Promise.resolve()
  for (const { text, file, seconds } of voiced) {
    await caption(page, text)
    cues.push({ out: outTime(now()), file, text, seconds })
    await sleep(seconds * 1000 + 250)
  }
  await work
}

function beat(page, title) {
  beats.push({ out: outTime(now()), title })
  return chip(page, title)
}

// Move the visible cursor to an element, then click it.
async function click(page, locator) {
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  if (box) {
    await ui(page, ([x, y]) => { const c = document.getElementById('wp-cursor'); c.style.left = `${x}px`; c.style.top = `${y}px` }, [box.x + box.width / 2, box.y + box.height / 2])
    await sleep(650)
    await ui(page, () => document.getElementById('wp-cursor').classList.add('down'))
  }
  await locator.click()
  await sleep(150)
  await ui(page, () => document.getElementById('wp-cursor').classList.remove('down'))
}

async function scrollTo(page, locator, offset = 90) {
  await locator.evaluate((el, off) => window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - off, behavior: 'smooth' }), offset)
  await sleep(900)
}

// A long AI step, sped up so it plays in about `seconds` of video; the badge counts real time.
async function fastWait(page, label, seconds, promise) {
  const start = now()
  await ui(page, (l) => {
    const el = document.getElementById('wp-speed')
    const t0 = Date.now()
    el.style.opacity = '1'
    clearInterval(window.__wpTimer)
    window.__wpTimer = setInterval(() => { el.textContent = `⏩ sped up · ${l} · real time ${Math.round((Date.now() - t0) / 1000)} s` }, 200)
  }, label)
  await promise
  const real = now() - start
  const speed = Math.max(1, real / seconds)
  segments.push({ at: start, speed })
  segments.sort((a, b) => a.at - b.at)
  setSpeed(1)
  await ui(page, ([l, r]) => {
    clearInterval(window.__wpTimer)
    document.getElementById('wp-speed').textContent = `✓ ${l} · real time ${r} s`
  }, [label, Math.round(real)])
  await sleep(1400)
  await ui(page, () => { document.getElementById('wp-speed').style.opacity = '0' })
  return Math.round(real)
}

// ---------------------------------------------------------------------------------------------
// The run
// ---------------------------------------------------------------------------------------------
const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1.2 })
const page = await context.newPage()
const tid = (id) => page.locator(`[data-testid="${id}"]`)
const cdp = await context.newCDPSession(page)
const frames = []
cdp.on('Page.screencastFrame', async ({ data, metadata, sessionId }) => {
  const file = path.join(FRAMES, `${String(frames.length).padStart(6, '0')}.jpg`)
  fs.writeFileSync(file, Buffer.from(data, 'base64'))
  frames.push({ file, t: metadata.timestamp })
  await cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
})

await page.goto(URL, { waitUntil: 'domcontentloaded' })
await tid('scenario-go-live').waitFor({ timeout: 120000 })
await page.waitForTimeout(1500)
await page.evaluate(OVERLAY)
await card(page, `<h1>WorkPilot AI</h1><h2>From knowledge to outcomes · Team GENI</h2>
  <div class="flow">Knowledge → Decision → <b>Approval</b> → Action → Outcome → Impact</div>
  <p>AI Builder Cup 2026 · Future of Work &amp; Enterprise Productivity</p>
  <p>Live on Google Cloud Run · Gemini via ADK · Vertex AI Search · Firestore · BigQuery</p>`)
await sleep(600)
await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 })
segments.length = 0
setSpeed(1)

// 0 · Title
await say(page, [
  'This is WorkPilot AI, from team GENI.',
  'It takes a team from scattered documents to an approved, executed and measured decision, live on Google Cloud.',
])
await card(page, '')
await sleep(700)

// 1 · Start
await beat(page, '1 · Ask')
await say(page, 'Maya Chen, the release manager for Customer Portal version twelve, asks one question.', {
  during: async () => {
    await click(page, tid('scenario-go-live'))
    const q = tid('question')
    await q.fill('')
    await q.pressSequentially('Are we ready to go live? What is blocking the release, who owns each action, and what must be completed before approval?', { delay: 14 })
  },
})
await click(page, tid('analyze-with-workpilot'))
const searchSecs = await fastWait(page, 'Vertex AI Search', 2, tid('source-card').first().waitFor({ timeout: 120000 }))

// 2 · Knowledge
await beat(page, '2 · Knowledge')
facts.docs = await tid('source-card').count()
await say(page, [
  `Vertex AI Search looked through the team's ${facts.docs} release documents in ${searchSecs < 2 ? 'about a second' : `${searchSecs} seconds`}.`,
  'Each card shows the real file, and what the search cited from it.',
], { during: async () => { await scrollTo(page, tid('source-cards'), 120) } })
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
await sleep(500)

// 3 · Analyze
await beat(page, '3 · Analyze')
await click(page, tid('next-analyze'))
const analyzeSecs = await fastWait(page, 'Gemini via ADK', 5, tid('next-decision').waitFor({ timeout: 240000 }))
facts.blockers = await tid('blocker-card').count()
facts.verdict = (await tid('verdict-status').innerText()).trim()
await say(page, [
  `Gemini, orchestrated by the Agent Development Kit, read the evidence in ${analyzeSecs} seconds.`,
  `The verdict: ${facts.verdict.toLowerCase()}. ${facts.blockers} blockers, each with an owner, a due date and its sources.`,
], { during: async () => { await scrollTo(page, tid('blocker-card').first(), 260) } })
await say(page, 'Click a source, and you see the exact passage behind the claim.', {
  during: async () => {
    await click(page, tid('blocker-card').first().locator('[data-testid="cite"]').first())
    await tid('evidence-drawer').waitFor()
  },
})
await sleep(1200)
await page.keyboard.press('Escape')
await sleep(400)

// 4 · Decision
await beat(page, '4 · Decision')
await click(page, tid('next-decision'))
await tid('decision-record').waitFor()
await page.evaluate(() => window.scrollTo({ top: 0 }))
facts.conditions = await tid('approval-conditions').locator('li').count().catch(() => 0)
await say(page, [
  'The decision is saved in Firestore as a Decision Record.',
  `A recommendation, its rationale, and ${facts.conditions || 'the'} approval conditions that must close before go-live.`,
], { during: async () => { await scrollTo(page, tid('approval-conditions'), 200) } })

// 5 · Action Contracts
await beat(page, '5 · Action Contracts')
await click(page, tid('prepare-contracts'))
await fastWait(page, 'Agent drafts contracts', 4, tid('contract').first().waitFor({ timeout: 240000 }))
facts.contracts = await tid('contract').count()
await say(page, [
  `The agent turns each blocker into an Action Contract: ${facts.contracts} of them, with owner, due date and expected outcome.`,
  'Each runs only after a person approves. This is the exact Gmail follow-up it would create. Nothing is sent.',
], {
  during: async () => {
    await scrollTo(page, tid('contract').first(), 120)
    await click(page, tid('contract').first().locator('[data-testid="preview-gmail"]'))
    await tid('gmail-preview').waitFor()
  },
})

// 6 · Human approval and execution
await beat(page, '6 · Human approval')
await click(page, tid('next-approval'))
await tid('approval-row').first().waitFor()
await page.evaluate(() => window.scrollTo({ top: 0 }))
await say(page, 'Now the human gate. The agent cannot approve or run anything itself.', {
  during: async () => {
    await click(page, tid('approve-all'))
    await page.locator('[data-testid="approval-row"][data-approval="Approved"]').nth(facts.contracts - 1).waitFor({ timeout: 60000 })
  },
})
await say(page, `Maya approved all ${facts.contracts}. One click runs them as real Gmail drafts.`, {
  during: async () => { await click(page, tid('run-all')) },
})
await fastWait(page, 'Gmail API', 3, page.locator('[data-testid="approval-row"][data-execution="Executed"]').nth(facts.contracts - 1).waitFor({ timeout: 120000 }))
await say(page, 'Each receipt is the ID that Gmail returned.')

// 7 · Outcomes, confirmed by a person
await beat(page, '7 · Outcome')
await click(page, tid('next-outcome'))
await tid('outcome-form').first().waitFor({ timeout: 60000 })
await say(page, 'On Outcome, Maya records what actually happened for each action.', {
  during: async () => {
    await scrollTo(page, tid('next-step'), 120)
    // Each line restates that action's own expected outcome, marked as demo input.
    const forms = tid('outcome-form')
    const n = await forms.count()
    await scrollTo(page, forms.first(), 200)
    for (let i = 0; i < n; i++) {
      const expected = ((await forms.nth(i).innerText()).match(/Expected:\s*(.+)/) || [])[1] || 'completed as expected'
      const line = `Demo run: done as expected. ${expected.trim()} Evidence attached to the ticket.`
      const box = forms.nth(i).locator('[data-testid="actual-outcome"]')
      if (i === 0) await box.pressSequentially(line, { delay: 6 })
      else await box.fill(line)
    }
  },
})
await click(page, tid('ask-agent-all'))
const forms = tid('outcome-form')
const n = await forms.count()
await fastWait(page, 'Agent proposes', 4, (async () => {
  for (let i = 0; i < n; i++) await forms.nth(i).locator('[data-testid="suggestion"]').waitFor({ timeout: 240000 })
})())
const proposed = []
for (let i = 0; i < n; i++) proposed.push(((await forms.nth(i).locator('[data-testid="suggestion"]').innerText()).match(/Agent proposes\s+([A-Z_ ]+?)\s+—/) || [])[1] || '')
facts.proposed = proposed
const allAchieved = proposed.every((x) => x.trim() === 'ACHIEVED')
await say(page, [
  allAchieved
    ? 'The agent reads each outcome against its contract and proposes achieved for all of them.'
    : `The agent proposes a status for each: ${proposed.map((x) => x.toLowerCase()).join(', ')}.`,
  'But the person decides and confirms. Only an outcome confirmed as achieved closes a condition.',
], {
  during: async () => {
    await scrollTo(page, forms.first(), 200)
    // The person's choice: achieved where the agent agrees; otherwise the agent's proposal stands.
    for (let i = 0; i < n; i++) {
      const choice = proposed[i].trim().replace(/ /g, '_') || 'ACHIEVED'
      await forms.nth(i).locator('[data-testid="outcome-status"]').selectOption(choice)
    }
  },
})
await click(page, tid('confirm-all'))
await fastWait(page, 'Agent resumes from Firestore', 4, page.locator('[data-testid="agent-message"][data-ok="true"]').waitFor({ timeout: 300000 }))

// 8 · Ready for approval
facts.now = (await tid('readiness-status').innerText()).trim()
await beat(page, `8 · ${facts.now.toLowerCase()}`)
await scrollTo(page, tid('before-after'), 110)
await say(page, [
  'With the last outcome confirmed, the agent resumed from its saved state and reassessed readiness.',
  `Not ready is now ${facts.now.toLowerCase()}. The final go-live decision stays with a person.`,
])

// 9 · Impact
await beat(page, '9 · Impact')
await scrollTo(page, tid('impact'), 110)
await tid('impact-formula').waitFor({ timeout: 60000 })
facts.hours = (await tid('impact-hours').innerText()).trim()
const formula = (await tid('impact-formula').innerText()).trim()
facts.baseline = (formula.match(/(\d+) min manual baseline/) || [])[1]
facts.run = (formula.match(/([\d.]+) s WorkPilot run/) || [])[1]
const spoken = facts.hours.replace(/hrs?$/, 'hours').replace(/min$/, 'minutes')
await say(page, [
  `The impact: about ${spoken} saved on this one decision.`,
  `That is a ${facts.baseline} minute manual baseline, minus WorkPilot's measured ${Math.round(facts.run)} second run, with every assumption shown.`,
], {
  during: async () => {
    await click(page, tid('impact-method').locator('summary'))
    await sleep(600)
    await scrollTo(page, tid('impact-breakdown'), 420)
  },
})

// 10 · Close
await chip(page, '')
await caption(page, '')
await card(page, `<h1>WorkPilot AI</h1><h2>Every claim cited · every action approved by a person · every outcome confirmed</h2>
  <div class="flow">Knowledge → Decision → <b>Approval</b> → Action → Outcome → Impact</div>
  <p>Live: <code>${URL.replace('https://', '')}</code></p>
  <p>Code: <code>github.com/CodeByKodi/2026aibuildercup-geni</code></p>
  <p>Team GENI · AI Builder Cup 2026</p>`)
await sleep(600)
await say(page, 'WorkPilot AI. From knowledge to outcomes, with people in control.')
await caption(page, '')
await sleep(1500)
const endAt = now()
await cdp.send('Page.stopScreencast')
const sessionId = new globalThis.URL(page.url()).searchParams.get('s')
await browser.close()

// ---------------------------------------------------------------------------------------------
// Assemble: frames → video on the output clock, voice cues → narration track, then mux.
// ---------------------------------------------------------------------------------------------
const total = outTime(endAt)
const list = ['ffconcat version 1.0']
for (let i = 0; i < frames.length; i++) {
  const a = i === 0 ? 0 : outTime(frames[i].t)
  const b = i + 1 < frames.length ? outTime(frames[i + 1].t) : total
  const d = Math.max(b - a, 0)
  if (d < 0.0005 && i + 1 < frames.length) continue
  list.push(`file '${frames[i].file}'`, `duration ${d.toFixed(4)}`)
}
list.push(`file '${frames[frames.length - 1].file}'`)
fs.writeFileSync(path.join(OUT, 'frames.txt'), list.join('\n'))
const silent = path.join(OUT, 'workpilot-demo-captions.mp4')
execFileSync('ffmpeg', ['-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', path.join(OUT, 'frames.txt'),
  '-vf', 'fps=30,scale=1920:1080:flags=lanczos,format=yuv420p', '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-movflags', '+faststart', silent])

let final = silent
if (VOICE) {
  const inputs = []
  const filters = []
  cues.forEach((c, i) => {
    inputs.push('-i', c.file)
    const ms = Math.round(c.out * 1000)
    filters.push(`[${i + 1}:a]${CLIP_FILTER},adelay=${ms}|${ms}[a${i}]`)
  })
  const mix = `${filters.join(';')};${cues.map((_, i) => `[a${i}]`).join('')}amix=inputs=${cues.length}:normalize=0,${MIX_FILTER}[aout]`
  final = path.join(OUT, 'workpilot-demo.mp4')
  execFileSync('ffmpeg', ['-y', '-v', 'error', '-i', silent, ...inputs, '-filter_complex', mix, '-map', '0:v', '-map', '[aout]',
    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-shortest', '-movflags', '+faststart', final])
}

// Timeline and narration, for the storyboard and for recording a human voice-over.
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
const lines = [`# Demo timeline (${mmss(total)} total)`, '', `Recorded ${new Date().toISOString()} from ${URL} · session ${sessionId}`, '',
  '| At | Step |', '|---|---|', ...beats.map((b) => `| ${mmss(b.out)} | ${b.title} |`), '',
  '## Narration (with cue times)', '', ...cues.map((c) => `- **${mmss(c.out)}** ${c.text}`), '',
  '## Facts read from the screen', '', '```json', JSON.stringify(facts, null, 2), '```']
fs.writeFileSync(path.join(OUT, 'timeline.md'), lines.join('\n'))
fs.writeFileSync(path.join(OUT, 'cues.json'), JSON.stringify(cues.map((c) => ({ ...c, file: c.file && path.basename(c.file) })), null, 2))
// No two voice clips may overlap.
for (let i = 0; i + 1 < cues.length; i++) {
  const end = cues[i].out + cues[i].seconds
  if (end > cues[i + 1].out + 0.05) console.error(`WARNING: voice overlap at ${mmss(cues[i].out)} (${(end - cues[i + 1].out).toFixed(2)} s)`)
}
fs.rmSync(FRAMES, { recursive: true, force: true })
console.log(JSON.stringify({ video: final, captionsOnly: silent, seconds: Math.round(total), session: sessionId, frames: frames.length, facts }, null, 2))
if (total > 180) console.error(`WARNING: ${mmss(total)} is over the 3:00 limit`)
