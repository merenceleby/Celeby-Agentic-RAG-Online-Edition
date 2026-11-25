import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'

const ms = (v) => (v == null ? 'No data' : `${Math.round(v * 1000)}ms`)
const pct = (v) => (v == null ? 'No data' : `${v}%`)
const pctN = (v) => (v == null ? 'n/a' : `${v}%`)
const RESULT = {
  rate_limit_minute: 'Minute limit', rate_limit_day: 'Daily limit', rate_limit_provider: 'Google limit',
  not_indexed: 'No index', llm_error: 'Model error',
}

function Tile({ label, value, sub, tone = '' }) {
  return (
    <div className={`tile ${tone}`}>
      <span className="tile-label">{label}</span>
      <strong className="tile-value">{value}</strong>
      {sub && <span className="tile-sub">{sub}</span>}
    </div>
  )
}

function RetrievalRow({ title, hint, r }) {
  return (
    <div className="eval-row">
      <h4>{title} <span className="muted">{hint}</span></h4>
      <div className="tiles five">
        <Tile label="Hit@1" value={pctN(r.hit_at_1)} sub="Right page ranked first" />
        <Tile label="Hit@3" value={pctN(r.hit_at_3)} sub="Right page in the top 3" />
        <Tile label="Hit@5" value={pctN(r.hit_at_5)} sub="Right page in the top 5" />
        <Tile label="MRR" value={r.mrr} sub="Higher means ranked earlier" />
        <Tile label="Search time" value={`${r.avg_ms}ms`} sub="Average per question" />
      </div>
    </div>
  )
}

export default function AdminPanel({ quota, refreshQuota, onBack }) {
  const [m, setM] = useState(null)
  const [recent, setRecent] = useState([])
  const [fb, setFb] = useState(null)
  const [error, setError] = useState('')
  const [ev, setEv] = useState({ retrieval: null, answers: null })
  const [qs, setQs] = useState([])
  const [genN, setGenN] = useState(5)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState(null)

  const load = useCallback(() => {
    refreshQuota()
    Promise.all([api.metrics(), api.recent(), api.feedbackList(), api.evalLatest(), api.evalQuestions()])
      .then(([a, b, c, d, e]) => { setM(a); setRecent(b); setFb(c); setEv(d); setQs(e.questions); setError('') })
      .catch((e) => setError(e.message))
  }, [refreshQuota])

  useEffect(() => {
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [load])

  const reset = async (what) => {
    const text = what === 'metrics' ? 'Reset all performance metrics?' : 'Reset all like/dislike feedback?'
    if (!window.confirm(text)) return
    await (what === 'metrics' ? api.resetMetrics() : api.resetFeedback())
    load()
  }

  async function act(label, fn, done) {
    setBusy(label); setMsg(null)
    try { const r = await fn(); setMsg({ ok: true, text: done(r) }); await load() }
    catch (e) { setMsg({ ok: false, text: e.message }) }
    finally { setBusy('') }
  }

  const h = fb?.summary.helpful ?? 0
  const nh = fb?.summary.not_helpful ?? 0
  const limitHits = m ? Object.entries(m.errors).filter(([k]) => k.startsWith('rate_limit')).reduce((a, [, n]) => a + n, 0) : 0

  return (
    <div className="shell admin-shell">
      <header className="admin-head">
        <h1>⚙️ Admin Panel</h1>
        <button className="back-btn" onClick={onBack}>← Back to Chat</button>
      </header>

      <div className="admin-body">
        {error && <div className="banner bad" role="alert">{error}</div>}
        {!m && !error && <div className="admin-card"><p className="muted">Loading…</p></div>}

        {m && (
          <>
            <section className="admin-card">
              <h2>📊 System Performance Metrics</h2>
              <p className="sub">Real-time statistics about your RAG system's performance and usage</p>
              <div className="tiles">
                <Tile label="Total queries" value={m.total_queries} sub="Questions answered" />
                <Tile label="Self-corrections" value={m.self_corrections} sub="Times the system improved its answer" />
                <Tile label="Correction rate" value={pct(m.correction_rate)} sub="% of Quality answers corrected" />
                <Tile label="Verified rate" value={pct(m.verified_rate)} sub="Quality answers backed by sources" />
                <Tile label="Avg latency" value={ms(m.avg_latency)} sub="Average response time" />
                <Tile label="P95 latency" value={ms(m.p95_latency)} sub="95% of responses under" />
                <Tile label="P99 latency" value={ms(m.p99_latency)} sub="99% of responses under" />
                <Tile label="Cache hit rate" value={pct(m.cache_hit_rate)} sub="Queries served from cache" />
                <Tile label="Error rate" value={pct(m.error_rate)} sub="Failed or rejected queries" />
              </div>
              <button className="outline-btn" onClick={() => reset('metrics')}>🔄 Reset Metrics</button>
            </section>

            <section className="admin-card">
              <h2>⏱ Free Tier Budget</h2>
              <p className="sub">Every model call counts against the Gemini API free tier limits</p>
              <div className="tiles">
                <Tile label="This minute" value={quota ? `${quota.used_last_minute} / ${quota.rpm_limit}` : '...'}
                  sub={quota?.minute_reset_in ? `Frees up in ${quota.minute_reset_in} s` : 'Model calls in the last minute'} />
                <Tile label="Today" value={quota ? `${quota.used_today} / ${quota.rpd_limit}` : '...'}
                  sub="Resets at midnight Pacific time" />
                <Tile label="Model calls used" value={m.llm_calls} sub="Since metrics were last reset" />
                <Tile label="Limit hits" value={limitHits} sub="Questions rejected by the limit" />
              </div>
            </section>

            <section className="admin-card">
              <h2>👥 User Feedback</h2>
              <p className="sub">Like/Dislike feedback from users on answer quality</p>
              <div className="tiles three">
                <Tile tone="green" label="👍 Likes" value={h} />
                <Tile tone="red" label="👎 Dislikes" value={nh} />
                <Tile label="Satisfaction rate" value={h + nh ? `${((100 * h) / (h + nh)).toFixed(1)}%` : 'No data'} sub="Users who liked answers" />
              </div>
              <button className="outline-btn" onClick={() => reset('feedback')}>🔄 Reset Feedback</button>
            </section>

            <section className="admin-card">
              <h2>🧪 Lite Evaluation</h2>
              <p className="sub">Quality checks that use no model calls, so they cost nothing on the free tier</p>

              <h3 className="eval-h">Retrieval check</h3>
              <p className="muted">Does the right page come back for your test questions? You have {qs.length} test {qs.length === 1 ? 'question' : 'questions'}.</p>
              <div className="eval-actions">
                <label>Questions to generate
                  <input className="num-input" type="number" min="1" max="20" value={genN}
                    onChange={(e) => setGenN(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
                </label>
                <button className="outline-btn" disabled={!!busy}
                  onClick={() => act('Generating questions', () => api.evalGenerate(genN), (r) => `Added ${r.added} test questions (${r.total} in total).`)}>
                  ✨ Generate test questions ({genN} model {genN === 1 ? 'call' : 'calls'})
                </button>
                <button className="grad-btn" disabled={!!busy || qs.length === 0}
                  onClick={() => act('Running retrieval evaluation', () => api.evalRetrieval(), (r) => `Checked ${r.questions} questions.`)}>
                  ▶ Run retrieval evaluation
                </button>
              </div>
              {busy && <div className="banner info" role="status">{busy}… This can take a minute.</div>}
              {msg && <div className={`banner ${msg.ok ? 'ok' : 'bad'}`} role={msg.ok ? 'status' : 'alert'}>{msg.text}</div>}

              {ev.retrieval ? (
                <>
                  <p className="muted">Last run: {new Date(ev.retrieval.ts * 1000).toLocaleString()}, {ev.retrieval.questions} questions, top {ev.retrieval.k} results.</p>
                  <RetrievalRow title="Vector search only" hint="(what Fast mode uses)" r={ev.retrieval.vector} />
                  <RetrievalRow title="Hybrid search and re-ranking" hint="(what Quality mode uses, without the query rewrite)" r={ev.retrieval.hybrid} />
                  {ev.retrieval.missed?.length > 0 && (
                    <details className="accordion">
                      <summary>🔎 Questions where the right page was missed ({ev.retrieval.missed.length})</summary>
                      <ul className="feedback-list">
                        {ev.retrieval.missed.map((q, i) => (
                          <li key={i}><strong>{q.question}</strong>
                            <span className="muted">Expected {q.source}{q.pages.length ? `, page ${q.pages.join(' or ')}` : ''}</span></li>
                        ))}
                      </ul>
                    </details>
                  )}
                </>
              ) : <p className="muted">No retrieval evaluation yet.</p>}

              <h3 className="eval-h">Answer checks</h3>
              <p className="muted">Looks at the answers saved in your chats and compares them with the sources they cited.</p>
              <div className="eval-actions">
                <button className="grad-btn" disabled={!!busy}
                  onClick={() => act('Checking answers', () => api.evalAnswers(30), (r) => `Checked ${r.answers} saved answers.`)}>
                  ▶ Check the latest 30 answers
                </button>
              </div>
              {ev.answers ? (
                <>
                  <p className="muted">Last run: {new Date(ev.answers.ts * 1000).toLocaleString()}.</p>
                  <div className="tiles">
                    <Tile label="Grounding" value={pctN(ev.answers.grounding)} sub="Answer sentences close to a source passage" />
                    <Tile label="Relevancy" value={pctN(ev.answers.relevancy)} sub="How closely answers match the question" />
                    <Tile label="Citation check" value={pctN(ev.answers.citation_valid)} sub="Answers whose [n] markers point to a real source" />
                    <Tile label="Answers scored" value={ev.answers.scored} sub={`${ev.answers.abstained} said they could not find it`} />
                  </div>
                  {ev.answers.worst?.length > 0 && (
                    <details className="accordion">
                      <summary>📉 Weakest answers ({ev.answers.worst.length})</summary>
                      <ul className="feedback-list">
                        {ev.answers.worst.map((w, i) => (
                          <li key={i}><strong>{w.question}</strong>
                            <span className="muted">Grounding {w.grounding}%, relevancy {w.relevancy}%</span></li>
                        ))}
                      </ul>
                    </details>
                  )}
                </>
              ) : <p className="muted">No answer check yet. Ask a few questions first.</p>}

              <details className="accordion">
                <summary>📝 View test questions ({qs.length})</summary>
                <ul className="feedback-list">
                  {qs.map((q, i) => (
                    <li key={i}><strong>{q.question}</strong>
                      <span className="muted">{q.source}{q.pages.length ? `, page ${q.pages.join(' or ')}` : ''}{q.generated ? ', generated by the model' : ''}</span></li>
                  ))}
                  {qs.length === 0 && <li className="muted">No test questions yet.</li>}
                </ul>
                <p className="muted">The questions live in <code>backend/data/eval/questions.json</code>. Writing them by hand gives the most trustworthy results.
                  Format: <code>{'[{"question": "Who is Nagini?", "source": "book.pdf", "pages": [651]}]'}</code></p>
              </details>

              <div className="info-box">
                <strong>💡 What do these scores mean?</strong>
                <p>Hit@k is the share of test questions where the expected page appears in the top k results, and MRR rewards ranking it first.
                  Grounding, relevancy and the citation check are estimates made with the local search models. They point out answers worth reading again,
                  they do not prove an answer is right or wrong. Generated questions come from the model itself, so treat them as a rough guide.</p>
              </div>
            </section>

            <section className="admin-card">
              <details className="accordion">
                <summary>📝 Recent Questions ({recent.length})</summary>
                <div className="scroll-x">
                  <table className="table">
                    <thead><tr><th>Time</th><th>Mode</th><th>Result</th><th className="num">ms</th><th className="num">Calls</th><th>Question</th></tr></thead>
                    <tbody>
                      {recent.map((r, i) => (
                        <tr key={i}>
                          <td>{new Date(r.ts * 1000).toLocaleTimeString()}</td>
                          <td>{r.mode === 'fast' ? 'Fast' : 'Quality'}</td>
                          <td>{r.ok ? (r.cache_hit ? 'Cached' : r.validated === 0 ? 'Unverified' : 'Answered') : (RESULT[r.error_kind] || 'Failed')}</td>
                          <td className="num">{r.latency != null ? Math.round(r.latency * 1000) : ''}</td>
                          <td className="num">{r.llm_calls ?? ''}</td>
                          <td className="clip">{r.query}</td>
                        </tr>
                      ))}
                      {recent.length === 0 && <tr><td colSpan="6" className="muted">No questions yet.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </details>
              {fb && fb.items.length > 0 && (
                <details className="accordion">
                  <summary>💬 Feedback details ({fb.items.length})</summary>
                  <ul className="feedback-list">
                    {fb.items.map((f) => (
                      <li key={f.message_id}>
                        <strong>{f.rating === 1 ? '👍 Like' : '👎 Dislike'}</strong>
                        <span className="muted">{f.question}</span>
                        {f.comment && <span>{f.comment}</span>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  )
}
