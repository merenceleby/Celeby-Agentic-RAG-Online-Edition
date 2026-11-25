export class ApiError extends Error {
  constructor(status, body) {
    const detail = typeof body?.detail === 'string' ? body.detail : ''
    super(body?.message || detail || `Request failed (${status})`)
    this.status = status
    this.body = body || {}
  }
}

async function toError(res) {
  let body = {}
  try { body = await res.json() } catch { /* gövde JSON değil */ }
  return new ApiError(res.status, body)
}

async function request(path, options) {
  const res = await fetch(`/api${path}`, options)
  if (!res.ok) throw await toError(res)
  return res.json()
}

const json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  authStatus: () => request('/auth/status'),
  login: (username, password) => request('/auth/login', json('POST', { username, password })),
  logout: () => request('/auth/logout', { method: 'POST' }),
  health: () => request('/health'),
  quota: () => request('/quota'),
  indexStatus: () => request('/index/status'),
  evalQuestions: () => request('/eval/questions'),
  evalGenerate: (n) => request(`/eval/generate?n=${n}`, { method: 'POST' }),
  evalRetrieval: () => request('/eval/retrieval', { method: 'POST' }),
  evalAnswers: (limit = 30) => request(`/eval/answers?limit=${limit}`, { method: 'POST' }),
  evalLatest: () => request('/eval/latest'),
  documents: () => request('/documents'),
  indexDocs: (reset = false) => request(`/index?reset=${reset}`, { method: 'POST' }),
  upload: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/upload', { method: 'POST', body: form })
  },
  deleteDoc: (name) => request(`/documents/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  chats: () => request('/chats'),
  chat: (id) => request(`/chats/${id}`),
  deleteChat: (id) => request(`/chats/${id}`, { method: 'DELETE' }),
  query: (payload) => request('/query', json('POST', payload)),
  feedback: (message_id, rating, comment) => request('/feedback', json('POST', { message_id, rating, comment })),
  feedbackList: () => request('/feedback'),
  metrics: () => request('/metrics'),
  resetMetrics: () => request('/metrics', { method: 'DELETE' }),
  resetFeedback: () => request('/feedback', { method: 'DELETE' }),
  recent: () => request('/metrics/recent'),
}

/** Fast mod: SSE akışı. handlers: { chat, sources, token, done, error } */
export async function streamQuery(payload, handlers, signal) {
  const res = await fetch('/api/query-stream', { ...json('POST', payload), signal })
  if (!res.ok) throw await toError(res)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let end
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      let event = 'message'
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (data) handlers[event]?.(JSON.parse(data))
    }
  }
}
