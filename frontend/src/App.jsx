import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import ChatSidebar from './components/ChatSidebar.jsx'
import ChatInterface from './components/ChatInterface.jsx'
import DocumentsList from './components/DocumentsList.jsx'
import AdminPanel from './components/AdminPanel.jsx'

const TAGS = ['Celeby did', 'Celeby money', 'Celeby blood'] // başlık altındaki üç kelime: buradan değiştir

export default function App() {
  const [view, setView] = useState('chat')
  const [chats, setChats] = useState([])
  const [chatId, setChatId] = useState(null)
  const [chatKey, setChatKey] = useState(0) // sadece kullanıcı sohbet değiştirince artar
  const [quota, setQuota] = useState(null)
  const [docsVersion, setDocsVersion] = useState(0)
  const [index, setIndex] = useState(null)

  const refreshChats = useCallback(() => api.chats().then(setChats).catch(() => {}), [])
  const refreshQuota = useCallback(() => api.quota().then(setQuota).catch(() => {}), [])

  const pollIndex = useCallback(() => api.indexStatus().then((st) => { setIndex(st); return st }).catch(() => null), [])
  useEffect(() => { pollIndex() }, [pollIndex])
  useEffect(() => {
    if (!index?.running) return undefined
    const t = setInterval(async () => {
      const st = await pollIndex()
      if (st && !st.running) setDocsVersion((v) => v + 1)  // bitince doküman listesini yenile
    }, 1000)
    return () => clearInterval(t)
  }, [index?.running, pollIndex])

  useEffect(() => {
    refreshChats()
    refreshQuota()
    const t = setInterval(refreshQuota, 15000)
    return () => clearInterval(t)
  }, [refreshChats, refreshQuota])

  const selectChat = (id) => { setChatId(id); setChatKey((k) => k + 1); setView('chat') }
  const removeChat = async (id) => {
    await api.deleteChat(id)
    if (id === chatId) selectChat(null)
    refreshChats()
  }

  return (
    <>
      {view === 'admin' && <AdminPanel quota={quota} refreshQuota={refreshQuota} onBack={() => setView('chat')} />}

      {/* Sohbet ekranı admin'deyken de açık kalır: akan cevap yarım kalmaz */}
      <div className={`shell app-shell ${view === 'admin' ? 'is-hidden' : ''}`}>
        <header className="hero">
          <span className="edition">Online Edition</span>
          <button className="admin-btn" onClick={() => setView('admin')}>⚙️ Admin</button>
          <h1><span aria-hidden="true">🤖</span> Celeby Agentic RAG <span aria-hidden="true">🤖</span></h1>
          <div className="tags">{TAGS.map((t) => <span key={t}>{t}</span>)}</div>
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
