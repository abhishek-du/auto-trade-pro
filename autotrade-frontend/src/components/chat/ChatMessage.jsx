import { useState } from 'react'
import { Copy, Check, Bot, User, Brain } from 'lucide-react'
import StockDataCard from './StockDataCard'

// ── Text renderer — parses markdown-lite and highlights finance tokens ────────

function renderContent(text, onSymbolClick) {
  if (!text) return null
  const lines = text.split('\n')
  return lines.map((line, li) => {
    if (!line.trim()) return <div key={li} className="h-2" />

    // Bullet
    const isBullet = /^[-•*]\s/.test(line)
    const content  = isBullet ? line.replace(/^[-•*]\s/, '') : line

    const spans = tokenize(content, onSymbolClick)

    if (isBullet) {
      return (
        <div key={li} className="flex items-start gap-2 my-0.5">
          <span className="text-accent mt-1.5 shrink-0" style={{ fontSize: 7 }}>●</span>
          <span className="text-slate-200 text-sm leading-relaxed">{spans}</span>
        </div>
      )
    }
    return <p key={li} className="text-slate-200 text-sm leading-relaxed my-0.5">{spans}</p>
  })
}

function tokenize(text, onSymbolClick) {
  const parts = []
  // Regex: **bold**, *italic*, BUY/SELL/HOLD badge, ₹price, +/-%, SYMBOL.NS chip
  const re = /(\*\*(.+?)\*\*|\*(.+?)\*|\b(BUY|SELL|HOLD|BULLISH|BEARISH)\b|₹[\d,]+(?:\.\d+)?(?:\s*(?:Cr|L|K))?|[+-]?\d+\.?\d*%|[A-Z]{3,10}\.(?:NS|BO))/g
  let last = 0, m
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**')) {
      parts.push(<strong key={m.index} className="font-semibold text-slate-100">{m[2]}</strong>)
    } else if (tok.startsWith('*')) {
      parts.push(<em key={m.index} className="italic text-slate-300">{m[3]}</em>)
    } else if (['BUY', 'BULLISH'].includes(tok)) {
      parts.push(<span key={m.index} className="signal-badge-buy mx-0.5">{tok}</span>)
    } else if (['SELL', 'BEARISH'].includes(tok)) {
      parts.push(<span key={m.index} className="signal-badge-sell mx-0.5">{tok}</span>)
    } else if (tok === 'HOLD') {
      parts.push(<span key={m.index} className="mx-0.5 px-1.5 py-0.5 rounded text-xs font-semibold bg-slate-700 text-slate-300">{tok}</span>)
    } else if (tok.startsWith('₹')) {
      parts.push(<span key={m.index} className="font-semibold" style={{ color: '#38BDF8' }}>{tok}</span>)
    } else if (tok.includes('%')) {
      const isPos = tok.startsWith('+') || (!tok.startsWith('-') && parseFloat(tok) > 0)
      parts.push(<span key={m.index} className={isPos ? 'price-positive' : 'price-negative'}>{tok}</span>)
    } else if (tok.includes('.NS') || tok.includes('.BO')) {
      parts.push(
        <button key={m.index} className="stock-chip mx-0.5"
          onClick={() => onSymbolClick?.(`Tell me about ${tok}`)}>
          {tok}
        </button>
      )
    } else {
      parts.push(tok)
    }
    last = m.index + tok.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

// ── Typing indicator ──────────────────────────────────────────────────────────

function TypingIndicator() {
  return (
    <div className="flex items-center gap-3">
      <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-white text-xs font-bold glass-panel">A</div>
      <div className="px-4 py-3 rounded-2xl rounded-tl-sm border border-border glass-panel">
        <div className="typing-indicator flex items-center gap-1">
          <span /><span /><span />
        </div>
        <p className="text-[10px] text-muted mt-1">Avishk is analysing...</p>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function ChatMessage({ message, isLast, loading, onSymbolClick }) {
  const [copied, setCopied] = useState(false)
  const [showData, setShowData] = useState(true)
  const [showReasoning, setShowReasoning] = useState(false)
  const hasReasoning = !!(message.reasoning && String(message.reasoning).trim())

  const isUser = message.role === 'user'
  const hasContexts = message.contexts && Object.keys(message.contexts).length > 0
  const hasBuySell = /\b(BUY|SELL|BULLISH|BEARISH|invest|recommend)\b/i.test(message.content || '')

  function copy() {
    navigator.clipboard.writeText(message.content || '')
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // Loading state
  if (isLast && loading) return <TypingIndicator />

  if (isUser) {
    return (
      <div className="flex justify-end gap-2 group">
        <div className="max-w-[75%]">
          <div className="chat-bubble-user px-4 py-2.5 text-sm text-white leading-relaxed">
            {message.content}
          </div>
          <p className="text-[10px] text-muted/50 text-right mt-1">
            {new Date(message.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
          </p>
        </div>
        <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center shrink-0 mt-0.5">
          <User size={14} className="text-slate-300" />
        </div>
      </div>
    )
  }

  // Assistant
  return (
    <div className="flex items-start gap-3 group">
      <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 text-white text-xs font-bold glass-panel">A</div>

      <div className="flex-1 min-w-0">
        <div className={`relative px-4 py-3 rounded-2xl rounded-tl-sm border ${message.isError ? 'border-red-500/30' : 'border-border'}`}
          style={{ background: '#0F1829' }}>

          {message.isError && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 font-semibold mb-2 inline-block">⚠ Error</span>
          )}

          <div className="prose-chat">
            {renderContent(message.content, onSymbolClick)}
          </div>

          {hasBuySell && (
            <p className="text-[10px] text-muted/50 italic mt-3 pt-2 border-t border-border/40">
              This is analysis, not financial advice. Do your own research before investing.
            </p>
          )}

          {/* Copy button */}
          <button onClick={copy}
            className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 p-1 rounded text-muted hover:text-slate-300 transition-all">
            {copied ? <Check size={12} className="text-profit" /> : <Copy size={12} />}
          </button>
        </div>

        {/* Model reasoning (gpt-oss) — collapsible, off by default */}
        {hasReasoning && (
          <div className="mt-1">
            <button
              onClick={() => setShowReasoning(p => !p)}
              className="text-[10px] text-accent/70 hover:text-accent flex items-center gap-1 mt-1 mb-1">
              <Brain size={11} />
              {showReasoning ? 'Hide' : 'Show'} reasoning
            </button>
            {showReasoning && (
              <div className="rounded-lg border border-accent/20 bg-accent/5 px-3 py-2 mb-1">
                <p className="text-[10px] uppercase tracking-wider text-accent/60 mb-1">Model reasoning</p>
                <pre className="text-[11px] text-slate-300 whitespace-pre-wrap leading-relaxed font-sans">
                  {String(message.reasoning).trim()}
                </pre>
              </div>
            )}
          </div>
        )}

        {/* Inline data cards */}
        {hasContexts && (
          <div className="mt-1">
            <button
              onClick={() => setShowData(p => !p)}
              className="text-[10px] text-muted/60 hover:text-muted flex items-center gap-1 mt-1 mb-1">
              {showData ? '▼' : '▶'} {showData ? 'Hide' : 'Show'} data used in this analysis
            </button>
            {showData && Object.entries(message.contexts).map(([sym, ctx]) => (
              <StockDataCard key={sym} symbol={sym} context={ctx} />
            ))}
          </div>
        )}

        <p className="text-[10px] text-muted/50 mt-1">
          {new Date(message.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
          {message.source === 'rule_based' && <span className="ml-2 text-amber-400/60">· Basic mode</span>}
        </p>
      </div>
    </div>
  )
}
