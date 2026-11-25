import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api istekleri backend'e yönlenir (CORS derdi yok, SSE akışı da çalışır).
// Docker dev modunda hedef VITE_API_TARGET=http://backend:8000 olarak verilir.
const target = process.env.VITE_API_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': target } },
})
