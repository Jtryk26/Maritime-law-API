import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// API-kald sendes til backend i udvikling, så frontend og backend
// kan køre på hver sin port uden CORS-besvær.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_API_TARGET || 'http://localhost:8000', changeOrigin: true },
      '/health': { target: process.env.VITE_API_TARGET || 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
