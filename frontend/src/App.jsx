import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import ChatSidebar from './components/ChatSidebar.jsx'
import ChatInterface from './components/ChatInterface.jsx'
import DocumentsList from './components/DocumentsList.jsx'
import AdminPanel from './components/AdminPanel.jsx'
import LoginScreen from './components/LoginScreen.jsx'

const TAGS = ['Celeby did', 'Celeby money', 'Celeby blood'] // başlık altındaki üç kelime: buradan değiştir

export default function App() {
  const [auth, setAuth] = useState(null)
  const [view, setView] = useState('chat')
  const [chats, setChats] = useState([])
  const [chatId, setChatId] = useState(null)
  const [chatKey, setChatKey] = useState(0) // sadece kullanıcı sohbet değiştirince artar
  const [quota, setQuota] = useState(null)
  const [docsVersion, setDocsVersion] = useState(0)
  const [index, setIndex] = useState(null)

  const ready = !!(auth && (!auth.enabled || auth.authenticated))

  useEffect(() => {
    api.authStatus().then(setAuth).catch(() => setAuth({ enabled: false, authenticated: true, username: null }))
  }, [])

  const refreshChats = useCallback(() => api.chats().then(setChats).catch(() => {}), [])
  const refreshQuota = useCallback(() => api.quota().then(setQuota).catch(() => {}), [])

  const pollIndex = useCallback(() => api.indexStatus().then((st) => { setIndex(st); return st }).catch(() => null), [])
  useEffect(() => { if (ready) pollIndex() }, [ready, pollIndex])
  useEffect(() => {
    if (!ready || !index?.running) return undefined
    const t = setInterval(async () => {
      const st = await pollIndex()
      if (st && !st.running) setDocsVersion((v) => v + 1)  // bitince doküman listesini yenile
    }, 1000)
    return () => clearInterval(t)
  }, [ready, index?.running, pollIndex])

  useEffect(() => {
    if (!ready) return undefined
    refreshChats()
    refreshQuota()
    const t = setInterval(refreshQuota, 15000)
    return () => clearInterval(t)
  }, [ready, refreshChats, refreshQuota])

  const selectChat = (id) => { setChatId(id); setChatKey((k) => k + 1); setView('chat') }
  const removeChat = async (id) => {
    await api.deleteChat(id)
    if (id === chatId) selectChat(null)
    refreshChats()
  }

  const handleLogout = async () => {
    await api.logout().catch(() => {})
    setAuth({ enabled: true, authenticated: false, username: null })
    setView('chat')
  }

  if (!auth) return <div className="shell login-wrap" />
  if (auth.enabled && !auth.authenticated) {
    return <LoginScreen onSuccess={(username) => setAuth({ enabled: true, authenticated: true, username })} />
  }

  return (
    <>
      {view === 'admin' && <AdminPanel quota={quota} refreshQuota={refreshQuota} onBack={() => setView('chat')} />}

      {/* Sohbet ekranı admin'deyken de açık kalır: akan cevap yarım kalmaz */}
      <div className={`shell app-shell ${view === 'admin' ? 'is-hidden' : ''}`}>
        <header className="hero">
          <div className="header-actions">
            {auth.enabled && <button className="admin-btn" onClick={handleLogout}>🚪 Logout</button>}
            <button className="admin-btn" onClick={() => setView('admin')}>⚙️ Admin</button>
          </div>
          <h1><span aria-hidden="true">🤖</span> Celeby Agentic RAG <span aria-hidden="true">🤖</span></h1>
          {/* <div className="tags">{TAGS.map((t) => <span key={t}>{t}</span>)}</div> */}
          <div className="edition-wrap"><span className="edition">Online Edition</span></div>
          <p className="tagline">Advanced RAG with Query Rewriting, Hybrid Search &amp; Re-ranking</p>
        </header>

        <main className="grid">
          <ChatSidebar chats={chats} chatId={chatId} onSelect={selectChat} onNew={() => selectChat(null)}
            onDelete={removeChat} quota={quota} />
          <ChatInterface key={chatKey} chatId={chatId}
            onChatCreated={setChatId}
            onActivity={() => { refreshChats(); refreshQuota() }}
            index={index} onIndexStarted={pollIndex} />
          <DocumentsList version={docsVersion} indexRunning={!!index?.running} onIndexStarted={pollIndex} />
        </main>
      </div>
    </>
  )
}
