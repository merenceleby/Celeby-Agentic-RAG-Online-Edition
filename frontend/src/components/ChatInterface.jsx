import { useEffect, useRef, useState } from 'react'
import { api, streamQuery, ApiError } from '../api.js'
import UploadBar from './UploadBar.jsx'

const MODES = {
  quality: { label: '🎯 Quality - self correcting',
    desc: 'Multi-query rewriting, validation and self-correction for maximum accuracy. Uses 3 model calls.' },
  fast: { label: '⚡ Fast - streaming RAG',
    desc: 'Hybrid search + re-ranking with a streamed answer. Quick, not self-verified. Uses 1 model call.' },
}

const CITE_RE = /(\[\d+(?:\s*,\s*\d+)*\])/g

function citedSet(text) {
  const s = new Set()
  if (!text) return s
  for (const m of text.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
    for (const n of m[1].split(',')) s.add(Number(n.trim()))
  }
  return s
}

function Answer({ text, onCite }) {
  return (
    <p className="answer">
      {text.split(CITE_RE).map((part, i) => {
        const m = /^\[(\d+(?:\s*,\s*\d+)*)\]$/.exec(part)
        if (!m) return part
        const nums = m[1].split(',').map((n) => Number(n.trim()))
        return (
          <span key={i}>
            {nums.map((n, j) => (
              <button key={j} className="cite" aria-label={`Show source ${n}`} onClick={() => onCite(n)}>{n}</button>
            ))}
          </span>
        )
      })}
    </p>
  )
}

const cut = (t, n) => (t.length > n ? `${t.slice(0, n)}...` : t)

function MetaChips({ meta, sources }) {
  if (!meta) return null
  const scores = (sources || []).map((s) => s.relevance_score ?? 0)
  const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null
  const calls = meta.llm_calls ?? 0
  const chips = []
  if (avg !== null) chips.push(['', `📊 Retrieval Score: ${(avg * 100).toFixed(1)}%`])
  if (meta.query_time != null) chips.push(['', `⏱ Response time: ${Math.round(meta.query_time * 1000)} ms`])
  if (meta.mode === 'fast') chips.push(['', '⚡ Fast mode, not verified'])
  else chips.push(meta.validated ? ['ok', '✅ Verified against sources'] : ['warn', '⚠️ Not verified, check the pages'])
  if (meta.correction_applied) chips.push(['', '🔁 Self-corrected'])
  if ((meta.warnings || []).includes('retry_skipped_rate_limit')) chips.push(['warn', '⏳ Retry skipped (request limit)'])
  if (meta.cache_hit) chips.push(['', '💾 From cache'])
  chips.push(['', `🧮 ${calls} model ${calls === 1 ? 'call' : 'calls'}`])
  return <div className="meta-chips">{chips.map(([k, t], i) => <span key={i} className={`meta-chip ${k}`}>{t}</span>)}</div>
}

function Sources({ sources, answer, open, setOpen, hi }) {
  const refs = useRef({})
  useEffect(() => { if (hi) refs.current[hi]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [hi, open])
  if (!sources?.length) return null
  const citedNums = citedSet(answer)
  const items = sources.map((s, i) => ({ s, num: i + 1 }))
  const cited = items.filter(({ num }) => citedNums.has(num))
  const other = items.filter(({ num }) => !citedNums.has(num))
  const card = ({ s, num }) => (
    <div key={num} ref={(el) => { refs.current[num] = el }} className={`src-card ${hi === num ? 'hi' : ''}`}>
      <span className="src-tag">[{num}] {cut(s.metadata?.source || '', 26)} - p.{s.metadata?.page}</span>
      <p>{cut(s.content, 420)}</p>
    </div>
  )
  return (
    <details className="src-box" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        📚 Sources ({cited.length ? `${cited.length} cited / ${sources.length} total` : sources.length})
      </summary>
      {cited.map(card)}
      {cited.length > 0 && other.length > 0 && (
        <div className="src-divider">Other retrieved sources ({other.length})</div>
      )}
      {other.map(card)}
    </details>
  )
}

function Turn({ q, a, onRate }) {
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(null)
  const [why, setWhy] = useState(false)
  const [sent, setSent] = useState(false)
  const [comment, setComment] = useState('')
  return (
    <div className="turn">
      <div className="msg user">{q.content}</div>
      {!a && <p className="pending">No answer was saved for this question.</p>}
      {a && (
        <div className="msg bot">
          {a.error && <div className="banner bad" role="alert">{a.error}</div>}
          {a.pending && !a.content && <p className="pending">{a.streaming ? 'Searching your documents…' : 'Rewriting, searching and checking the answer…'}</p>}
          {a.content && <Answer text={a.content} onCite={(n) => { setOpen(true); setHi(n) }} />}
          {a.streaming && a.content && <span className="caret" aria-hidden="true" />}
          {a.id && !a.streaming && (
            <div className="rate" role="group" aria-label="Rate this answer">
              <button aria-pressed={a.rating === 1} onClick={() => { onRate(a, 1); setWhy(false) }}>👍 Like</button>
              <button aria-pressed={a.rating === -1} onClick={() => { onRate(a, -1); setWhy(true); setSent(false) }}>👎 Dislike</button>
            </div>
          )}
          {why && !sent && (
            <div className="why">
              <input value={comment} maxLength={500} placeholder="What went wrong? (optional)" aria-label="What went wrong?"
                onChange={(e) => setComment(e.target.value)} />
              <button onClick={() => { if (comment.trim()) onRate(a, -1, comment.trim()); setSent(true) }}>Send</button>
            </div>
          )}
          {!a.streaming && <MetaChips meta={a.metadata} sources={a.sources} />}
          {!a.streaming && !a.pending && a.content && <Sources sources={a.sources} answer={a.content} open={open} setOpen={setOpen} hi={hi} />}
        </div>
      )}
    </div>
  )
}

const fromDb = (m) => ({ key: `m${m.id}`, id: m.id, role: m.role, content: m.content,
  sources: m.sources, metadata: m.metadata, rating: m.rating })

export default function ChatInterface({ chatId, onChatCreated, onActivity, index, onIndexStarted }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('quality')
  const [busy, setBusy] = useState(false)
  const [limit, setLimit] = useState(null)
  const [now, setNow] = useState(Date.now())
  const [error, setError] = useState('')
  const [modelsReady, setModelsReady] = useState(true)
  const currentChat = useRef(chatId)
  const abortRef = useRef(null)
  const endRef = useRef(null)

  useEffect(() => {
    if (chatId) api.chat(chatId).then((c) => setMessages(c.messages.map(fromDb))).catch((e) => setError(e.message))
    return () => abortRef.current?.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [messages.length])

  // Embedding/reranker modelleri ilk açılışta iner; bitene kadar bilgi ver
  useEffect(() => {
    let stop = false
    let timer
    const check = async () => {
      try {
        const h = await api.health()
        if (stop) return
        setModelsReady(h.models_ready)
        if (!h.models_ready) timer = setTimeout(check, 3000)
      } catch { if (!stop) timer = setTimeout(check, 5000) }
    }
    check()
    return () => { stop = true; clearTimeout(timer) }
  }, [])

  useEffect(() => {
    if (!limit || limit.kind === 'day') return
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [limit])

  const remaining = limit && limit.kind !== 'day' ? Math.max(0, Math.ceil((limit.until - now) / 1000)) : 0
  useEffect(() => { if (limit && limit.kind !== 'day' && remaining === 0) setLimit(null) }, [limit, remaining])
  const blocked = remaining > 0

  async function send() {
    const query = input.trim()
    if (!query || busy || blocked) return
    setError(''); setBusy(true); setInput('')
    const id = `t${Date.now()}${Math.random().toString(36).slice(2, 7)}`
    const ak = `${id}a`
    setMessages((m) => [...m,
      { key: `${id}u`, role: 'user', content: query },
      { key: ak, role: 'assistant', content: '', sources: [], metadata: null, pending: true, streaming: mode === 'fast' }])
    const patch = (fn) => setMessages((m) => m.map((x) => (x.key === ak ? fn(x) : x)))
    const payload = { query, mode, chat_id: currentChat.current ?? null }
    const isNew = payload.chat_id == null
    const adopt = (cid) => { currentChat.current = cid; if (isNew) onChatCreated(cid) }

    try {
      if (mode === 'fast') {
        const ctrl = new AbortController()
        abortRef.current = ctrl
        await streamQuery(payload, {
          chat: ({ chat_id }) => adopt(chat_id),
          sources: (s) => patch((x) => ({ ...x, sources: s })),
          token: ({ text }) => patch((x) => ({ ...x, content: x.content + text, pending: false })),
          done: (meta) => patch((x) => ({ ...x, metadata: meta, id: meta.message_id, streaming: false, pending: false })),
          error: (e) => {
            patch((x) => ({ ...x, streaming: false, pending: false, error: e.message }))
            if (e.retry_after) setLimit({ kind: e.kind, until: Date.now() + e.retry_after * 1000, message: e.message })
          },
        }, ctrl.signal)
      } else {
        const res = await api.query(payload)
        adopt(res.metadata.chat_id)
        patch((x) => ({ ...x, content: res.answer, sources: res.sources, metadata: res.metadata,
          id: res.metadata.message_id, pending: false }))
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        setMessages((m) => m.filter((x) => !x.key.startsWith(id)))  // sunucu bu mesajı kaydetmedi
        setInput(query)
        if (e instanceof ApiError && e.status === 429) {
          setLimit({ kind: e.body.kind, until: Date.now() + (e.body.retry_after || 60) * 1000, message: e.body.message })
        } else {
          setError(e.message)
        }
      }
    } finally {
      setBusy(false)
      onActivity()
    }
  }

  async function rate(msg, rating, comment) {
    try {
      await api.feedback(msg.id, rating, comment)
      setMessages((m) => m.map((x) => (x.key === msg.key ? { ...x, rating } : x)))
    } catch (e) { setError(e.message) }
  }

  // Yarım kalmış (cevabı kaydedilmemiş) sorular eşleşmeyi bozmasın diye kullanıcı mesajından başlat
  const turns = []
  for (const m of messages) {
    if (m.role === 'user') turns.push([m, null])
    else if (turns.length) turns[turns.length - 1][1] = m
  }

  return (
    <section className="center-card">
      <div className="controls">
        <UploadBar index={index} onStarted={onIndexStarted} />
        <div className="mode-row">
          <div className="mode-pick">
            <label htmlFor="mode">Response Mode</label>
            <select id="mode" value={mode} onChange={(e) => setMode(e.target.value)}>
              {Object.entries(MODES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>
          <div className="mode-desc">{MODES[mode].desc}</div>
        </div>
      </div>

      <div className="messages">
        {turns.length === 0 && (
          <div className="empty">
            <h3>Ask a question about your documents</h3>
            <p>Each answer lists the pages it came from, so you can check it.</p>
          </div>
        )}
        {turns.map(([q, a]) => <Turn key={q.key} q={q} a={a} onRate={rate} />)}
        <div ref={endRef} />
      </div>

      {(limit || error || !modelsReady) && (
        <div className="alerts">
          {!modelsReady && <div className="banner info" role="status">The search models are still loading (the first start downloads about 1 GB). Questions work once this message disappears.</div>}
          {limit && (
            <div className="banner warn" role="alert">
              {limit.kind === 'day'
                ? <>{limit.message} Both modes need model calls, so questions work again after the reset.</>
                : <>{limit.message} {blocked ? `You can send again in ${remaining} s.` : ''}</>}
            </div>
          )}
          {error && <div className="banner bad" role="alert">{error}</div>}
        </div>
      )}

      <div className="input-bar">
        <input value={input} placeholder="Ask a question about your documents..." aria-label="Your question"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
        <button className="grad-btn send" onClick={send} disabled={busy || blocked || !input.trim()}>
          {busy ? 'Working…' : blocked ? `Wait ${remaining} s` : 'Send'}
        </button>
      </div>
    </section>
  )
}
