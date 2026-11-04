import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Served by the Python backend at /app/ (src/server.py), proxied in dev.
export default defineConfig({
  plugins: [react()],
  base: '/app/',
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
})
