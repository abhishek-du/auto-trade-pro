import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const backendUrl = process.env.VITE_API_URL || 'http://localhost:8000';
const backendWs  = backendUrl.replace(/^http/, 'ws');

// HMR describes how the BROWSER reaches the dev server, and that differs by
// how the app is being served:
//   • locally      → http://localhost:5173, plain ws, same port
//   • via the ELB  → https://<elb-host>, TLS terminated at :443
//
// This was hardcoded to the ELB, so opening the app on localhost told the HMR
// client to dial wss://vnad5173.elb.cisinlive.com:443 — which cannot work from
// there and logged "WebSocket closed without opened" on every page load.
// Omitting `hmr` lets Vite derive it from window.location, which is right for
// localhost; the ELB still needs the explicit override because the page port
// (443) differs from the dev-server port Vite would otherwise assume.
//
// Serving through the ELB:  VITE_HMR_HOST=vnad5173.elb.cisinlive.com npm run dev
// (same process.env convention as VITE_API_URL above — vite.config runs in
// Node, so these come from the shell, not from .env)
const hmrHost = process.env.VITE_HMR_HOST;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Harmless when unused, and required for the ELB to be accepted as a Host.
    allowedHosts: ['vnad5173.elb.cisinlive.com'],
    ...(hmrHost
      ? { hmr: { protocol: 'wss', host: hmrHost, clientPort: 443 } }
      : {}),
    proxy: {
      '/api': backendUrl,
      '/ws':  { target: backendWs, ws: true },
    },
  },
})
