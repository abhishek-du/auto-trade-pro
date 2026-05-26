import { useRef, useEffect, useState, useCallback } from 'react'
import { Send, Loader2 } from 'lucide-react'

export default function ChatInput({ value, onChange, onSend, onSuggestionClick, loading, suggestions, placeholder }) {
  const textareaRef = useRef(null)
  const [autocomplete, setAutocomplete] = useState([])
  const [showAuto, setShowAuto]         = useState(false)
  const [autoLoading, setAutoLoading]   = useState(false)
  const debounceRef = useRef(null)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }, [value])

  // Autocomplete: trigger when last word looks like a ticker
  const fetchSuggestions = useCallback((text) => {
    const words = text.trim().split(/\s+/)
    const last  = words[words.length - 1].toUpperCase()
    if (last.length < 2 || /[^A-Z0-9&]/.test(last)) {
      setShowAuto(false)
      return
    }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setAutoLoading(true)
      try {
        const res  = await fetch(`/api/v1/chat/suggest/${encodeURIComponent(last)}`)
        const data = await res.json()
        if (Array.isArray(data) && data.length) {
          setAutocomplete(data)
          setShowAuto(true)
        } else {
          setShowAuto(false)
        }
      } catch {
        setShowAuto(false)
      } finally {
        setAutoLoading(false)
      }
    }, 280)
  }, [])

  function handleChange(e) {
    onChange(e.target.value)
    fetchSuggestions(e.target.value)
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!loading && value.trim()) {
        setShowAuto(false)
        onSend()
      }
    }
    if (e.key === 'Escape') setShowAuto(false)
  }

  function pickAutoComplete(sym) {
    const words   = value.trim().split(/\s+/)
    words[words.length - 1] = sym
    onChange(words.join(' ') + ' ')
    setShowAuto(false)
    textareaRef.current?.focus()
  }

  const showSuggestions = !value.trim() && suggestions?.length > 0

  return (
    <div className="border-t border-border px-4 py-3" style={{ background: '#080D1A' }}>
      {/* Suggestion pills (shown when input is empty) */}
      {showSuggestions && (
        <div className="flex gap-2 overflow-x-auto no-scrollbar pb-2.5">
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => onSuggestionClick?.(s)}
              className="shrink-0 text-[11px] px-3 py-1.5 rounded-full border border-border text-muted hover:text-slate-200 hover:border-accent/40 hover:bg-accent/5 transition-all whitespace-nowrap"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Autocomplete dropdown */}
      {showAuto && autocomplete.length > 0 && (
        <div className="mb-2 rounded-xl border border-border overflow-hidden"
          style={{ background: '#0F1829' }}>
          {autocomplete.slice(0, 5).map((item) => (
            <button
              key={item.symbol}
              onClick={() => pickAutoComplete(item.symbol)}
              className="w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-white/5 transition-colors text-left"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-bold text-slate-200 shrink-0">{item.symbol.replace('.NS', '').replace('.BO', '')}</span>
                <span className="text-[10px] text-muted truncate">{item.display_name}</span>
              </div>
              {item.price > 0 && (
                <div className="flex items-center gap-2 shrink-0 ml-4">
                  <span className="text-xs text-slate-300 tabular-nums">
                    ₹{item.price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                  </span>
                  <span className={`text-[10px] font-semibold tabular-nums ${item.change_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {item.change_pct >= 0 ? '+' : ''}{item.change_pct?.toFixed(2)}%
                  </span>
                </div>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-2">
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            onBlur={() => setTimeout(() => setShowAuto(false), 150)}
            placeholder={placeholder || 'Ask Avishk about any NSE stock…'}
            disabled={loading}
            className="w-full resize-none rounded-xl border border-border px-4 py-3 text-sm text-slate-200 placeholder-muted/50 outline-none focus:border-accent/50 transition-colors disabled:opacity-50"
            style={{ background: '#0F1829', minHeight: 44, maxHeight: 120 }}
          />
        </div>
        <button
          onClick={() => { if (!loading && value.trim()) { setShowAuto(false); onSend() } }}
          disabled={loading || !value.trim()}
          className="shrink-0 w-11 h-11 rounded-xl flex items-center justify-center transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}
        >
          {loading
            ? <Loader2 size={16} className="text-white animate-spin" />
            : <Send size={16} className="text-white" />
          }
        </button>
      </div>
      <p className="text-[10px] text-muted/40 text-center mt-2">
        Enter to send · Shift+Enter for new line · For education only, not financial advice
      </p>
    </div>
  )
}
