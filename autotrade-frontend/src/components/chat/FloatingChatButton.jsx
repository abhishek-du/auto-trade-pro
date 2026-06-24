import { useState, useRef, useEffect } from 'react'
import { MessageSquare, X, Send, Loader2 } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import { apiFetch } from '../../api/client'

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
      const data = await apiFetch('/api/v1/chat/message', {
        method: 'POST',
        body:   JSON.stringify({ message: txt, history }),
      })
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
    <div className="fixed bottom-20 right-4 md:bottom-6 md:right-6 z-50 flex flex-col items-end gap-3 pointer-events-none">
      {/* Mini chat drawer */}
      {open && (
        <div className="w-[calc(100vw-2rem)] md:w-80 max-w-sm rounded-2xl overflow-hidden glass-panel slide-in-right pointer-events-auto flex flex-col shadow-[0_8px_30px_rgb(0,0,0,0.5)]">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/5 bg-white/5 backdrop-blur-md">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold shadow-[0_0_15px_rgba(41,121,255,0.4)] glass-panel">A</div>
              <div>
                <p className="text-sm font-bold text-slate-100 tracking-tight">Avishk AI</p>
                <p className="text-[10px] text-cyan uppercase tracking-widest font-semibold opacity-90">NSE Stock Analyst</p>
              </div>
            </div>
            <button onClick={() => setOpen(false)} className="text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 p-1.5 rounded-full transition-all">
              <X size={16} />
            </button>
          </div>

          {/* Messages */}
          <div className="h-72 overflow-y-auto px-4 py-4 space-y-3 relative" style={{ scrollbarWidth: 'thin' }}>
            {messages.length === 0 ? (
              <div className="absolute inset-0 flex flex-col items-center justify-center p-4 text-center">
                <div className="w-12 h-12 rounded-full mb-3 flex items-center justify-center opacity-30 shadow-[0_0_20px_rgba(41,121,255,0.3)] glass-panel">
                  <MessageSquare size={20} className="text-white" />
                </div>
                <p className="text-xs text-slate-300 leading-relaxed font-medium">
                  Ask me anything about NSE stocks.
                </p>
                <p className="text-[11px] text-slate-500 mt-1">Try: "Is HDFC Bank a BUY?"</p>
              </div>
            ) : (
              messages.map(m => (
                <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] text-xs leading-relaxed px-3.5 py-2.5 ${m.role === 'user' ? 'chat-bubble-user text-white' : 'chat-bubble-bot text-slate-200'}`}>
                    {m.content}
                  </div>
                </div>
              ))
            )}
            {loading && (
              <div className="flex justify-start">
                <div className="chat-bubble-bot px-3 py-2">
                  <div className="typing-indicator">
                    <span></span><span></span><span></span>
                  </div>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="px-3 py-3 border-t border-white/5 bg-black/20 backdrop-blur-md flex items-center gap-2">
            <input
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Ask about a stock…"
              disabled={loading}
              className="flex-1 text-xs rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-slate-200 placeholder-slate-500 outline-none focus:border-accent/50 focus:bg-white/10 transition-all disabled:opacity-50"
            />
            <button onClick={send} disabled={loading || !input.trim()}
              className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 transition-all disabled:opacity-40 hover:scale-105 active:scale-95 shadow-[0_0_10px_rgba(41,121,255,0.3)] glass-panel">
              {loading
                ? <Loader2 size={14} className="text-white animate-spin" />
                : <Send size={14} className="text-white ml-0.5" />
              }
            </button>
          </div>

          {/* Link to full page */}
          <div className="px-3 pb-3 bg-black/20 text-center">
            <a href="/chat" className="text-[10px] text-cyan hover:text-white font-medium tracking-wide transition-colors inline-flex items-center gap-1">
              Open full Avishk chat <span className="text-xs">→</span>
            </a>
          </div>
        </div>
      )}

      {/* FAB */}
      <div className="pointer-events-auto relative">
        <button
          onClick={() => setOpen(o => !o)}
          className="relative w-14 h-14 md:w-16 md:h-16 rounded-full shadow-[0_8px_25px_rgba(41,121,255,0.4)] flex items-center justify-center transition-transform duration-300 hover:scale-110 active:scale-95 z-10 glass-panel"
          title="Ask Avishk AI"
        >
          {open ? <X size={24} className="text-white drop-shadow-md" /> : <MessageSquare size={24} className="text-white drop-shadow-md" />}
          {!open && unread > 0 && (
            <span className="absolute -top-1 -right-1 w-5 h-5 md:w-6 md:h-6 rounded-full bg-loss flex items-center justify-center text-[10px] md:text-xs font-bold text-white shadow-lg border-2 border-[#040812]">
              {unread}
            </span>
          )}
        </button>
        {/* Avishk pulse ring */}
        {!open && (
          <div className="absolute inset-0 rounded-full arjun-pulse pointer-events-none z-0" />
        )}
      </div>
    </div>
  )
}
