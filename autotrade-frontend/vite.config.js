import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const backendUrl = process.env.VITE_API_URL || 'http://localhost:8000';
const backendWs  = backendUrl.replace(/^http/, 'ws');

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    allowedHosts: ['vnad5173.elb.cisinlive.com'],
    hmr: {
      protocol: 'wss',
      host: 'vnad5173.elb.cisinlive.com',
      clientPort: 443
    },
    proxy: {
      '/api': backendUrl,
      '/ws':  { target: backendWs, ws: true },
    },
  },
})
