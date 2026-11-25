import { useState } from 'react'
import { api } from '../api.js'

export default function LoginScreen({ onSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [banned, setBanned] = useState(false)

  async function submit(e) {
    e.preventDefault()
    if (!username.trim() || !password || busy) return
    setError('')
    setBusy(true)
    try {
      const res = await api.login(username.trim(), password)
      onSuccess(res.username)
    } catch (err) {
      setError(err.message || 'Fail.')
      setBanned(err.status === 429)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="shell login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1><span aria-hidden="true">🤖</span> Celeby Agentic RAG</h1>
        <div className="edition-wrap"><span className="edition">Online Edition</span></div>
        <p className="muted login-sub">Please Login</p>
        {error && <div className={`banner ${banned ? 'bad' : 'warn'}`} role="alert">{error}</div>}
        <label>
          <span>Username</span>
          <input value={username} autoComplete="username" autoFocus disabled={busy || banned}
            onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>
          <span>Password</span>
          <input type="password" value={password} autoComplete="current-password" disabled={busy || banned}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        <button className="grad-btn" type="submit" disabled={busy || banned || !username.trim() || !password}>
          {busy ? 'Checking…' : 'Login'}
        </button>
      </form>
    </div>
  )
}
