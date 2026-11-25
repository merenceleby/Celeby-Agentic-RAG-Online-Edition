import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'

export default function DocumentsList({ version, indexRunning, onIndexStarted }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState(null)

  const load = useCallback(() => api.documents().then(setData)
    .catch((e) => setNotice({ bad: true, text: e.message })), [])
  useEffect(() => { load() }, [load, version])

  async function run(label, fn, done) {
    setBusy(label); setNotice(null)
    try { const r = await fn(); setNotice({ text: done(r) }) }
    catch (e) { setNotice({ bad: true, text: e.message }) }
    finally { setBusy(''); load() }
  }

  async function startIndex(reset) {
    setNotice(null)
    try {
      const r = await api.indexDocs(reset)
      if (r.started) await onIndexStarted()
      else setNotice({ text: r.message })
    } catch (e) { setNotice({ bad: true, text: e.message }) }
  }
  const indexNew = () => startIndex(false)
  const rebuild = () => window.confirm('Delete the whole index and build it again from the PDFs? This can take a while.') && startIndex(true)
  const remove = (name) => window.confirm(`Delete ${name} and remove it from the index?`) &&
    run(`Deleting ${name}`, () => api.deleteDoc(name), () => `${name} deleted.`)

  return (
    <aside className="panel docs-panel">
      <div className="docs-head">
        <h2>📄 Uploaded Documents</h2>
        <button className="refresh" onClick={load} aria-label="Refresh documents">🔄</button>
      </div>

      {busy && <div className="banner info" role="status">{busy}…</div>}
      {notice && <div className={`banner ${notice.bad ? 'bad' : 'ok'}`} role={notice.bad ? 'alert' : 'status'}>{notice.text}</div>}

      {data && data.files.length === 0 && <p className="hint">No PDFs yet. Upload one to start asking questions.</p>}
      {data?.files.map((f) => (
        <div className="doc-card" key={f.name}>
          <span className="doc-icon" aria-hidden="true">📄</span>
          <div className="doc-info">
            <strong title={f.name}>{f.name}</strong>
            <small>{f.indexed ? `${f.chunks} chunks` : 'Not indexed yet'}</small>
          </div>
          <button className="doc-del" disabled={!!busy || indexRunning} aria-label={`Delete ${f.name}`} onClick={() => remove(f.name)}>❌</button>
        </div>
      ))}

      <div className="doc-actions">
        <button className="text-btn" disabled={!!busy || indexRunning} onClick={indexNew}>Index new files</button>
        <button className="text-btn" disabled={!!busy || indexRunning} onClick={rebuild}>Rebuild index</button>
      </div>
      <p className="tiny">PDFs you copy into <code>backend/data/documents</code> appear here. Choose Index new files to add them.</p>
    </aside>
  )
}
