function fmtDate(ts) {
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`
}

function Meter({ label, used, limit }) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0
  return (
    <div className="q-row">
      <div className="q-label"><span>{label}</span><span>{used} / {limit}</span></div>
      <div className="q-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used}>
        <div className="q-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export default function ChatSidebar({ chats, chatId, onSelect, onNew, onDelete, quota }) {
  return (
    <aside className="panel chats-panel">
      <div className="panel-head">
        <h2>💬 Chats</h2>
        <button className="new-chat" onClick={onNew}>➕ New Chat</button>
      </div>

      <ul className="chat-list" aria-label="Chat history">
        {chats.length === 0 && <li className="hint">Your questions will be saved here.</li>}
        {chats.map((c) => (
          <li key={c.id} className={`chat-item ${c.id === chatId ? 'active' : ''}`}>
            <button className="chat-open" onClick={() => onSelect(c.id)}>
              <strong>{c.title}</strong>
              <small>{c.message_count} messages • {fmtDate(c.updated_at)}</small>
            </button>
            <button className="chat-del" aria-label={`Delete chat: ${c.title}`}
              onClick={() => window.confirm('Delete this chat?') && onDelete(c.id)}>❌</button>
          </li>
        ))}
      </ul>

      {quota && (
        <div className="quota">
          <Meter label="Model calls this minute" used={quota.used_last_minute} limit={quota.rpm_limit} />
          <Meter label="Model calls today" used={quota.used_today} limit={quota.rpd_limit} />
        </div>
      )}
    </aside>
  )
}
