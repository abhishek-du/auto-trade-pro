import { useRef, useEffect } from 'react'
import { Bot, Trash2, AlertTriangle, X } from 'lucide-react'
import { useStockChat } from '../hooks/useStockChat'
import ChatMessage  from '../components/chat/ChatMessage'
import ChatInput    from '../components/chat/ChatInput'
import ChatSidebar  from '../components/chat/ChatSidebar'

function EmptyState({ onSuggestionClick }) {
  const CARDS = [
    { icon: '📈', label: 'Buy/Sell Analysis',  q: 'Should I buy HDFC Bank now?' },
    { icon: '📊', label: 'Technical Analysis', q: 'Technical analysis for Reliance' },
    { icon: '💰', label: 'Fundamentals',       q: 'Is TCS overvalued at current price?' },
    { icon: '📰', label: 'News & Sentiment',   q: 'Latest news on Infosys' },
    { icon: '🔍', label: 'Stock Comparison',   q: 'Compare SBI vs ICICI Bank' },
    { icon: '🌊', label: 'Market Overview',    q: 'NIFTY 50 trend analysis today' },
  ]
  return (
    <div className="flex flex-col items-center justify-center h-full py-12 px-8">
      <div className="w-16 h-16 rounded-2xl flex items-center justify-center text-white text-2xl font-black mb-6 shadow-xl"
        style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}>A</div>
      <h2 className="text-xl font-bold text-slate-100 mb-1">Meet Avishk</h2>
      <p className="text-sm text-muted text-center mb-8 max-w-sm">
        Your AI-powered NSE stock analyst. Ask about prices, technicals, fundamentals, or get buy/sell recommendations backed by live data.
      </p>
      <div className="grid grid-cols-2 gap-2 w-full max-w-md">
        {CARDS.map((c, i) => (
          <button
            key={i}
            onClick={() => onSuggestionClick(c.q)}
            className="flex items-start gap-2.5 px-4 py-3 rounded-xl border border-border text-left hover:border-accent/40 hover:bg-accent/5 transition-all group"
            style={{ background: '#0F1829' }}
          >
            <span className="text-lg shrink-0">{c.icon}</span>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-slate-300 group-hover:text-slate-100">{c.label}</p>
              <p className="text-[10px] text-muted/70 mt-0.5 truncate">{c.q}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

export default function StockChat() {
  const {
    messages, input, setInput,
    loading, error, noAiBanner, setNoAiBanner,
    sendMessage, clearChat,
    activeContexts, messagesEndRef,
    suggestions,
  } = useStockChat()

  const onlyWelcome = messages.length === 1 && messages[0].isWelcome

  return (
    <div className="flex h-full min-h-0 -m-6">

      {/* Main chat column */}
      <div className="flex flex-col flex-1 min-w-0">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0"
          style={{ background: 'linear-gradient(135deg,rgba(29,78,216,0.12),rgba(8,145,178,0.06))' }}>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center text-white text-sm font-black"
              style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}>A</div>
            <div>
              <h1 className="text-base font-bold text-slate-100">Avishk — AI Stock Analyst</h1>
              <p className="text-[11px] text-muted">Live NSE data · Technical & fundamental analysis</p>
            </div>
            <span className="ml-2 flex items-center gap-1 text-[10px] text-profit">
              <span className="w-1.5 h-1.5 rounded-full bg-profit animate-pulse" />
              Live
            </span>
          </div>
          <button
            onClick={clearChat}
            className="flex items-center gap-1.5 text-[11px] text-muted hover:text-slate-300 transition-colors px-3 py-1.5 rounded-lg hover:bg-white/5"
          >
            <Trash2 size={12} />
            Clear
          </button>
        </div>

        {/* No-AI banner */}
        {noAiBanner && (
          <div className="mx-4 mt-3 flex items-center gap-2.5 px-4 py-2.5 rounded-xl border border-warn/20 shrink-0"
            style={{ background: 'rgba(245,158,11,0.07)' }}>
            <AlertTriangle size={14} className="text-warn shrink-0" />
            <p className="text-xs text-warn/80 flex-1">
              Running in basic mode — add <code className="bg-black/30 px-1 rounded text-warn">GROQ_API_KEY</code> to .env for full AI responses.
            </p>
            <button onClick={() => setNoAiBanner(false)} className="text-warn/60 hover:text-warn">
              <X size={12} />
            </button>
          </div>
        )}

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {onlyWelcome ? (
            <EmptyState onSuggestionClick={sendMessage} />
          ) : (
            <>
              {messages.map((msg, i) => (
                <ChatMessage
                  key={msg.id}
                  message={msg}
                  isLast={i === messages.length - 1}
                  loading={loading && i === messages.length - 1}
                  onSymbolClick={sendMessage}
                />
              ))}
              {loading && messages[messages.length - 1]?.role === 'user' && (
                <ChatMessage
                  message={{ role: 'assistant', content: '', timestamp: new Date().toISOString() }}
                  isLast
                  loading
                  onSymbolClick={sendMessage}
                />
              )}
            </>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <ChatInput
          value={input}
          onChange={setInput}
          onSend={sendMessage}
          onSuggestionClick={sendMessage}
          loading={loading}
          suggestions={onlyWelcome ? [] : suggestions}
          placeholder="Ask Avishk about any NSE stock…"
        />
      </div>

      {/* Sidebar */}
      <ChatSidebar
        activeContexts={activeContexts}
        onSuggestionClick={sendMessage}
      />
    </div>
  )
}
