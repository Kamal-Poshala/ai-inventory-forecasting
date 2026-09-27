import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { chunkSizeWarningLimit: 1000 },
  server: {
    proxy: { '/api': process.env.API_URL ?? 'http://localhost:8000' },
  },
})
