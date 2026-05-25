import { useState, useEffect, useRef } from 'react'
import { X, Search, Plus } from 'lucide-react'
import toast from 'react-hot-toast'
import { formatINR } from '../../utils/indianFormat'

export default function AddHoldingModal({ onClose, onAdd, searchStocks }) {
  const [query,    setQuery]    = useState('')
  const [results,  setResults]  = useState([])
  const [selected, setSelected] = useState(null)
  const [qty,      setQty]      = useState('')
  const [price,    setPrice]    = useState('')
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [notes,    setNotes]    = useState('')
  const [searching, setSearching] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const debounceRef = useRef(null)
  const inputRef    = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    if (!query.trim()) { setResults([]); return }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try {
        const res = await searchStocks(query)
        setResults(res || [])
      } finally {
        setSearching(false)
      }
    }, 300)
    return () => clearTimeout(debounceRef.current)
  }, [query])

  const totalInvested = qty && price ? parseFloat(qty) * parseFloat(price) : 0

  async function handleSubmit(e) {
    e.preventDefault()
    if (!selected || !qty || !price) {
      toast.error('Select a stock and fill in quantity and price')
      return
    }
    setSubmitting(true)
    try {
      await onAdd({
        symbol:     selected.symbol,
        quantity:   parseFloat(qty),
        price:      parseFloat(price),
        trade_date: tradeDate,
        notes,
      })
      toast.success(`${selected.ticker} added to portfolio`)
      onClose()
    } catch (err) {
      toast.error(err.message || 'Failed to add holding')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />

      {/* Drawer */}
      <div className="fixed right-0 top-0 h-full w-full max-w-md z-50 flex flex-col bg-panel border-l border-border shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="text-slate-100 font-bold text-base flex items-center gap-2">
            <Plus size={16} className="text-cyan" /> Add Holding
          </h2>
          <button onClick={onClose} className="p-1.5 rounded text-muted hover:text-white hover:bg-white/10 transition-colors">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* Stock search */}
          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-2">Search Stock</label>
            {selected ? (
              <div className="flex items-center justify-between p-3 rounded-lg border border-accent/30 bg-accent/5">
                <div>
                  <p className="text-slate-100 font-semibold">{selected.name}</p>
                  <p className="text-muted text-xs">{selected.ticker} · {selected.sector}</p>
                </div>
                <button
                  type="button"
                  onClick={() => { setSelected(null); setQuery('') }}
                  className="text-muted hover:text-white p-1"
                >
                  <X size={14} />
                </button>
              </div>
            ) : (
              <div className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="e.g. Reliance, HDFC, TCS…"
                  className="w-full pl-9 pr-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
                />
                {searching && (
                  <div className="absolute right-3 top-1/2 -translate-y-1/2 w-3 h-3 border border-muted border-t-transparent rounded-full animate-spin" />
                )}
                {results.length > 0 && (
                  <div className="absolute top-full mt-1 left-0 right-0 z-10 bg-panel border border-border rounded-lg shadow-xl overflow-hidden">
                    {results.map(r => (
                      <div
                        key={r.symbol}
                        onClick={() => { setSelected(r); setResults([]) }}
                        className="flex items-center justify-between px-4 py-2.5 cursor-pointer hover:bg-white/5 transition-colors"
                      >
                        <div>
                          <p className="text-slate-200 text-sm font-medium">{r.name}</p>
                          <p className="text-muted text-[10px]">{r.ticker}</p>
                        </div>
                        <span className="text-[10px] text-muted bg-white/5 px-2 py-0.5 rounded">{r.sector}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Qty / Price row */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Quantity</label>
              <input
                type="number" min="0.001" step="any"
                value={qty}
                onChange={e => setQty(e.target.value)}
                placeholder="e.g. 10"
                className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Buy Price (₹)</label>
              <input
                type="number" min="0.01" step="any"
                value={price}
                onChange={e => setPrice(e.target.value)}
                placeholder="e.g. 2450.50"
                className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
              />
            </div>
          </div>

          {/* Date */}
          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Trade Date</label>
            <input
              type="date"
              value={tradeDate}
              onChange={e => setTradeDate(e.target.value)}
              max={new Date().toISOString().slice(0, 10)}
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 outline-none focus:border-accent/50"
            />
          </div>

          {/* Notes */}
          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Notes (optional)</label>
            <input
              value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder="e.g. Long term hold"
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
            />
          </div>

          {/* Total invested preview */}
          {totalInvested > 0 && (
            <div className="flex items-center justify-between p-3 rounded-lg bg-accent/5 border border-accent/20">
              <span className="text-sm text-muted">Total Invested</span>
              <span className="text-base font-bold text-slate-100">{formatINR(totalInvested)}</span>
            </div>
          )}
        </form>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-border flex gap-3">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 py-2.5 rounded-lg border border-border text-sm text-muted hover:text-white hover:border-border/80 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !selected || !qty || !price}
            className="flex-1 py-2.5 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {submitting ? 'Adding…' : 'Add Holding'}
          </button>
        </div>
      </div>
    </>
  )
}
