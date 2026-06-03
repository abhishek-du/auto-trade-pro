import { useState, useEffect, useRef } from 'react'
import { X, Search, Plus, TrendingUp, Wallet } from 'lucide-react'
import toast from 'react-hot-toast'
import { formatINR } from '../../utils/indianFormat'
import { apiFetch } from '../../api/client'

// ── Stock tab ──────────────────────────────────────────────────────────────────

function StockTab({ onAdd, onClose, searchStocks }) {
  const [query,     setQuery]     = useState('')
  const [results,   setResults]   = useState([])
  const [selected,  setSelected]  = useState(null)
  const [qty,       setQty]       = useState('')
  const [price,     setPrice]     = useState('')
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [notes,     setNotes]     = useState('')
  const [searching, setSearching] = useState(false)
  const [submitting,setSubmitting]= useState(false)
  const debounceRef = useRef(null)
  const inputRef    = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    if (!query.trim()) { setResults([]); return }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try { setResults((await searchStocks(query)) || []) }
      finally { setSearching(false) }
    }, 300)
    return () => clearTimeout(debounceRef.current)
  }, [query])

  const total = qty && price ? parseFloat(qty) * parseFloat(price) : 0

  async function handleSubmit(e) {
    e.preventDefault()
    if (!selected || !qty || !price) { toast.error('Select a stock and fill in quantity and price'); return }
    setSubmitting(true)
    try {
      await onAdd({ symbol: selected.symbol, quantity: parseFloat(qty), price: parseFloat(price), trade_date: tradeDate, notes })
      toast.success(`${selected.ticker} added to portfolio`)
      onClose()
    } catch (err) { toast.error(err.message || 'Failed to add holding') }
    finally { setSubmitting(false) }
  }

  return (
    <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
      <div>
        <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-2">Search Stock</label>
        {selected ? (
          <div className="flex items-center justify-between p-3 rounded-lg border border-accent/30 bg-accent/5">
            <div>
              <p className="text-slate-100 font-semibold">{selected.name}</p>
              <p className="text-muted text-xs">{selected.ticker} · {selected.sector}</p>
            </div>
            <button type="button" onClick={() => { setSelected(null); setQuery('') }} className="text-muted hover:text-white p-1"><X size={14} /></button>
          </div>
        ) : (
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input ref={inputRef} value={query} onChange={e => setQuery(e.target.value)}
              placeholder="e.g. Reliance, HDFC, TCS…"
              className="w-full pl-9 pr-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50" />
            {searching && <div className="absolute right-3 top-1/2 -translate-y-1/2 w-3 h-3 border border-muted border-t-transparent rounded-full animate-spin" />}
            {(results.length > 0 || (!searching && query.trim().length >= 2)) && (
              <div className="absolute top-full mt-1 left-0 right-0 z-10 bg-panel border border-border rounded-lg shadow-xl overflow-hidden">
                {results.map(r => (
                  <div key={r.symbol} onClick={() => { setSelected(r); setResults([]) }}
                    className="flex items-center justify-between px-4 py-2.5 cursor-pointer hover:bg-white/5 transition-colors">
                    <div>
                      <p className="text-slate-200 text-sm font-medium">{r.name}</p>
                      <p className="text-muted text-[10px]">{r.ticker}</p>
                    </div>
                    <span className="text-[10px] text-muted bg-white/5 px-2 py-0.5 rounded">{r.sector}</span>
                  </div>
                ))}
                {!searching && results.length === 0 && query.trim().length >= 2 && (
                  <div onClick={() => { const t = query.trim().toUpperCase(); setSelected({ name: t, symbol: t + '.NS', ticker: t, sector: 'Other' }); setResults([]) }}
                    className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-white/5 transition-colors border-t border-border/50">
                    <div className="w-6 h-6 rounded bg-cyan/10 flex items-center justify-center shrink-0">
                      <span className="text-cyan text-[10px] font-bold">NS</span>
                    </div>
                    <div>
                      <p className="text-slate-200 text-sm font-medium">Use <span className="text-cyan font-bold">{query.trim().toUpperCase()}.NS</span> directly</p>
                      <p className="text-muted text-[10px]">Add as custom NSE symbol</p>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Quantity</label>
          <input type="number" min="0.001" step="any" value={qty} onChange={e => setQty(e.target.value)} placeholder="e.g. 10"
            className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Buy Price (₹)</label>
          <input type="number" min="0.01" step="any" value={price} onChange={e => setPrice(e.target.value)} placeholder="e.g. 2450.50"
            className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50" />
        </div>
      </div>
      <div>
        <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Trade Date</label>
        <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)} max={new Date().toISOString().slice(0, 10)}
          className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 outline-none focus:border-accent/50" />
      </div>
      <div>
        <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Notes (optional)</label>
        <input value={notes} onChange={e => setNotes(e.target.value)} placeholder="e.g. Long term hold"
          className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50" />
      </div>
      {total > 0 && (
        <div className="flex items-center justify-between p-3 rounded-lg bg-accent/5 border border-accent/20">
          <span className="text-sm text-muted">Total Invested</span>
          <span className="text-base font-bold text-slate-100">{formatINR(total)}</span>
        </div>
      )}
      <div className="flex gap-3 pt-2">
        <button type="button" onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-border text-sm text-muted hover:text-white hover:border-border/80 transition-colors">Cancel</button>
        <button type="submit" disabled={submitting || !selected || !qty || !price}
          className="flex-1 py-2.5 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity">
          {submitting ? 'Adding…' : 'Add Holding'}
        </button>
      </div>
    </form>
  )
}

// ── Mutual Fund tab ────────────────────────────────────────────────────────────

function MFTab({ onAdd, onClose }) {
  const [query,     setQuery]     = useState('')
  const [results,   setResults]   = useState([])
  const [selected,  setSelected]  = useState(null)
  const [units,     setUnits]     = useState('')
  const [nav,       setNav]       = useState('')
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [notes,     setNotes]     = useState('')
  const [searching, setSearching] = useState(false)
  const [navLoading,setNavLoading]= useState(false)
  const [submitting,setSubmitting]= useState(false)
  const debounceRef = useRef(null)
  const inputRef    = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    if (!query.trim()) { setResults([]); return }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try {
        // apiFetch returns parsed JSON and throws on non-2xx — no .ok / .json() shim.
        const data = await apiFetch(`/api/v1/portfolios/search/mf?q=${encodeURIComponent(query)}`)
        setResults(Array.isArray(data) ? data : [])
      } catch { setResults([]) }
      finally { setSearching(false) }
    }, 300)
    return () => clearTimeout(debounceRef.current)
  }, [query])

  async function selectFund(fund) {
    setSelected(fund)
    setResults([])
    setNavLoading(true)
    try {
      const d = await apiFetch(`/api/v1/portfolios/search/mf/${fund.scheme_code}/nav`)
      if (d?.nav != null) setNav(d.nav.toFixed(4))
    } catch {}
    finally { setNavLoading(false) }
  }

  const total = units && nav ? parseFloat(units) * parseFloat(nav) : 0

  async function handleSubmit(e) {
    e.preventDefault()
    if (!selected || !units || !nav) { toast.error('Select a fund and fill in units and NAV'); return }
    setSubmitting(true)
    try {
      await onAdd({
        symbol:       `MF:${selected.scheme_code}`,
        quantity:     parseFloat(units),
        price:        parseFloat(nav),
        trade_date:   tradeDate,
        notes,
        company_name: selected.scheme_name,
        sector:       selected.category || 'Mutual Fund',
      })
      toast.success(`${selected.scheme_name.slice(0, 30)}… added to portfolio`)
      onClose()
    } catch (err) { toast.error(err.message || 'Failed to add fund') }
    finally { setSubmitting(false) }
  }

  return (
    <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
      <div>
        <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-2">Search Mutual Fund</label>
        {selected ? (
          <div className="flex items-start justify-between p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-slate-100 font-semibold text-sm leading-snug">{selected.scheme_name}</p>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">{selected.category}</span>
                <span className="text-muted text-[10px]">{selected.scheme_code}</span>
              </div>
            </div>
            <button type="button" onClick={() => { setSelected(null); setQuery(''); setNav('') }} className="text-muted hover:text-white p-1 shrink-0"><X size={14} /></button>
          </div>
        ) : (
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input ref={inputRef} value={query} onChange={e => setQuery(e.target.value)}
              placeholder="e.g. Parag Parikh, HDFC Midcap, ELSS…"
              className="w-full pl-9 pr-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50" />
            {searching && <div className="absolute right-3 top-1/2 -translate-y-1/2 w-3 h-3 border border-muted border-t-transparent rounded-full animate-spin" />}
            {results.length > 0 && (
              <div className="absolute top-full mt-1 left-0 right-0 z-10 bg-panel border border-border rounded-lg shadow-xl overflow-hidden max-h-60 overflow-y-auto">
                {results.map(r => (
                  <div key={r.scheme_code} onClick={() => selectFund(r)}
                    className="flex items-center justify-between px-4 py-2.5 cursor-pointer hover:bg-white/5 transition-colors">
                    <div className="flex-1 min-w-0 pr-2">
                      <p className="text-slate-200 text-sm font-medium truncate">{r.scheme_name}</p>
                      <p className="text-muted text-[10px]">{r.scheme_code}</p>
                    </div>
                    <span className="text-[9px] text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded shrink-0">{r.category}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Units Purchased</label>
          <input type="number" min="0.001" step="any" value={units} onChange={e => setUnits(e.target.value)} placeholder="e.g. 125.432"
            className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">
            Purchase NAV (₹)
            {navLoading && <span className="ml-1 text-muted text-[9px] normal-case">fetching…</span>}
          </label>
          <input type="number" min="0.01" step="any" value={nav} onChange={e => setNav(e.target.value)} placeholder="e.g. 42.50"
            className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50" />
          {nav && !navLoading && (
            <p className="text-muted text-[10px] mt-0.5">Current NAV auto-fetched · edit if using historical NAV</p>
          )}
        </div>
      </div>

      <div>
        <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Purchase Date</label>
        <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)} max={new Date().toISOString().slice(0, 10)}
          className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 outline-none focus:border-accent/50" />
      </div>

      <div>
        <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Notes (optional)</label>
        <input value={notes} onChange={e => setNotes(e.target.value)} placeholder="e.g. SIP investment"
          className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50" />
      </div>

      {total > 0 && (
        <div className="flex items-center justify-between p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
          <span className="text-sm text-muted">Total Invested</span>
          <span className="text-base font-bold text-slate-100">{formatINR(total)}</span>
        </div>
      )}

      <div className="flex gap-3 pt-2">
        <button type="button" onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-border text-sm text-muted hover:text-white transition-colors">Cancel</button>
        <button type="submit" disabled={submitting || !selected || !units || !nav}
          className="flex-1 py-2.5 rounded-lg bg-gradient-to-r from-emerald-600 to-teal-500 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity">
          {submitting ? 'Adding…' : 'Add Fund'}
        </button>
      </div>
    </form>
  )
}

// ── Main modal ─────────────────────────────────────────────────────────────────

export default function AddHoldingModal({ onClose, onAdd, searchStocks }) {
  const [tab, setTab] = useState('stock')

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <div className="fixed right-0 top-0 h-full w-full max-w-md z-50 flex flex-col bg-panel border-l border-border shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <h2 className="text-slate-100 font-bold text-base flex items-center gap-2">
            <Plus size={16} className="text-cyan" /> Add Holding
          </h2>
          <button onClick={onClose} className="p-1.5 rounded text-muted hover:text-white hover:bg-white/10 transition-colors"><X size={16} /></button>
        </div>

        {/* Tabs */}
        <div className="flex shrink-0 border-b border-border">
          <button
            onClick={() => setTab('stock')}
            className={`flex items-center gap-2 flex-1 py-3 text-sm font-semibold transition-all ${
              tab === 'stock'
                ? 'text-cyan border-b-2 border-cyan bg-cyan/5'
                : 'text-muted hover:text-slate-300'
            }`}
          >
            <TrendingUp size={14} className="mx-auto" />
            <span>Stock / ETF</span>
          </button>
          <button
            onClick={() => setTab('mf')}
            className={`flex items-center gap-2 flex-1 py-3 text-sm font-semibold transition-all ${
              tab === 'mf'
                ? 'text-emerald-400 border-b-2 border-emerald-400 bg-emerald-500/5'
                : 'text-muted hover:text-slate-300'
            }`}
          >
            <Wallet size={14} className="mx-auto" />
            <span>Mutual Fund</span>
          </button>
        </div>

        {/* Body */}
        {tab === 'stock'
          ? <StockTab onAdd={onAdd} onClose={onClose} searchStocks={searchStocks} />
          : <MFTab onAdd={onAdd} onClose={onClose} />
        }
      </div>
    </>
  )
}
