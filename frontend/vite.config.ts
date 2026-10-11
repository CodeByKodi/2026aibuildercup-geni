import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on :8000 in development (uvicorn workpilot.api:app); in production
// FastAPI serves this build from the same origin.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
