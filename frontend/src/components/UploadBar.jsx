import { useState } from 'react'
import { api } from '../api.js'

function IndexStatus({ index }) {
  const [dismissed, setDismissed] = useState(null)
  if (!index) return null
  if (index.running) {
    const pct = index.pages ? Math.round((index.page / index.pages) * 100) : 0
    return (
      <div className="banner info" role="status">
        <div>
          Indexing {index.file}: page {index.page} of {index.pages || '...'}
          {index.file_total > 1 ? ` (file ${index.file_index} of ${index.file_total})` : ''}
        </div>
        <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <div style={{ width: `${pct}%` }} />
        </div>
      </div>
    )
  }
  if (index.error) return <div className="banner bad" role="alert">Indexing failed: {index.error}</div>
  const recent = index.finished_at && Date.now() / 1000 - index.finished_at < 60
  if (recent && index.done.length && dismissed !== index.finished_at) {
    return (
      <div className="banner ok" role="status">
        Indexed {index.done.map((d) => `${d.file} (${d.chunks} chunks)`).join(', ')}.{' '}
        <button className="text-btn" onClick={() => setDismissed(index.finished_at)}>Dismiss</button>
      </div>
    )
  }
  return null
}

export default function UploadBar({ index, onStarted }) {
  const [file, setFile] = useState(null)
  const [inputKey, setInputKey] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function upload() {
    setBusy(true); setError('')
    try {
      await api.upload(file)
      setFile(null); setInputKey((k) => k + 1)
      await onStarted()
    } catch (e) {
      setError(e.message)
    } finally { setBusy(false) }
  }

  const running = !!index?.running
  return (
    <div>
      <div className="upload-row">
        <input key={inputKey} type="file" accept=".pdf,application/pdf" aria-label="Choose a PDF"
          onChange={(e) => setFile(e.target.files[0] || null)} />
        <button className="grad-btn" disabled={!file || busy || running} onClick={upload}>
          📄 Upload &amp; Index PDF
        </button>
      </div>
      {error && <div className="banner bad" role="alert">{error}</div>}
      <IndexStatus index={index} />
    </div>
  )
}
