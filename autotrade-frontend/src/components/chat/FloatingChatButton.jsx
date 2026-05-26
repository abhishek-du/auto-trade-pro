import { useState, useRef, useEffect } from 'react'
import { MessageSquare, X, Send, Loader2 } from 'lucide-react'
import { useLocation } from 'react-router-dom'

const MAX_MINI_MESSAGES = 20

export default function FloatingChatButton() {
  const location = useLocation()
  const [open, setOpen]         = useState(false)
  const [messages, setMessages] = useState([])
  const [input, setInput]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [unread, setUnread]     = useState(0)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  // Hide on full chat page
  if (location.pathname === '/chat') return null

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, open])

  useEffect(() => {
    if (open) {
      setUnread(0)
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [open])

  async function send() {
    const txt = input.trim()
    if (!txt || loading) return
    setInput('')
    const userMsg = { role: 'user', content: txt, id: Date.now() }
    setMessages(prev => [...prev, userMsg].slice(-MAX_MINI_MESSAGES))
    setLoading(true)
    try {
      const history = messages.slice(-6).map(m => ({ role: m.role, content: m.content }))
      const res  = await fetch('/api/v1/chat/message', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ message: txt, history }),
      })
      const data = await res.json()
      const botMsg = { role: 'assistant', content: data.reply, id: Date.now() + 1 }
      setMessages(prev => [...prev, botMsg].slice(-MAX_MINI_MESSAGES))
      if (!open) setUnread(n => n + 1)
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant', content: 'Sorry, something went wrong.', id: Date.now() + 1,
      }].slice(-MAX_MINI_MESSAGES))
    } finally {
      setLoading(false)
    }
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-3">
      {/* Mini chat drawer */}
      {open && (
        <div className="w-80 rounded-2xl border border-border overflow-hidden shadow-2xl fade-in"
          style={{ background: '#0A1120' }}>

          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border"
            style={{ background: 'linear-gradient(135deg,rgba(29,78,216,0.4),rgba(8,145,178,0.2))' }}>
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-full flex items-center justify-center text-white text-xs font-bold"
                style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}>A</div>
              <div>
                <p className="text-xs font-bold text-slate-100">Avishk AI</p>
                <p className="text-[10px] text-muted">NSE Stock Analyst</p>
              </div>
            </div>
            <button onClick={() => setOpen(false)} className="text-muted hover:text-slate-300 transition-colors">
              <X size={14} />
            </button>
          </div>

          {/* Messages */}
          <div className="h-64 overflow-y-auto px-3 py-3 space-y-2" style={{ scrollbarWidth: 'thin' }}>
            {messages.length === 0 ? (
              <p className="text-[11px] text-muted/60 text-center mt-8 leading-relaxed">
                Ask me anything about NSE stocks.<br />
                <span className="text-muted/40">Try: "Is HDFC Bank a BUY?"</span>
              </p>
            ) : (
              messages.map(m => (
                <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] text-[11px] leading-relaxed px-3 py-2 rounded-xl ${
                    m.role === 'user'
                      ? 'text-white rounded-br-sm'
                      : 'text-slate-200 rounded-bl-sm border border-border'
                  }`}
                    style={m.role === 'user'
                      ? { background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }
                      : { background: '#0F1829' }
                    }>
                    {m.content}
                  </div>
                </div>
              ))
            )}
            {loading && (
              <div className="flex justify-start">
                <div className="px-3 py-2 rounded-xl rounded-bl-sm border border-border text-[10px] text-muted"
                  style={{ background: '#0F1829' }}>
                  Avishk is thinking…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="px-3 py-3 border-t border-border flex items-center gap-2">
            <input
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Ask about a stock…"
              disabled={loading}
              className="flex-1 text-xs rounded-lg border border-border px-3 py-2 text-slate-200 placeholder-muted/50 outline-none focus:border-accent/50 disabled:opacity-50"
              style={{ background: '#080D1A' }}
            />
            <button onClick={send} disabled={loading || !input.trim()}
              className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-all disabled:opacity-40"
              style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}>
              {loading
                ? <Loader2 size={12} className="text-white animate-spin" />
                : <Send size={12} className="text-white" />
              }
            </button>
          </div>

          {/* Link to full page */}
          <div className="px-3 pb-2 text-center">
            <a href="/chat" className="text-[10px] text-accent/60 hover:text-accent transition-colors">
              Open full Avishk chat →
            </a>
          </div>
        </div>
      )}

      {/* FAB */}
      <button
        onClick={() => setOpen(o => !o)}
        className="relative w-14 h-14 rounded-full shadow-xl flex items-center justify-center transition-all hover:scale-105 active:scale-95"
        style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}
        title="Ask Avishk AI"
      >
        {open ? <X size={22} className="text-white" /> : <MessageSquare size={22} className="text-white" />}
        {!open && unread > 0 && (
          <span className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-loss flex items-center justify-center text-[10px] font-bold text-white">
            {unread}
          </span>
        )}
        {/* Avishk pulse ring */}
        {!open && (
          <span className="absolute inset-0 rounded-full arjun-pulse pointer-events-none" />
        )}
      </button>
    </div>
  )
}
