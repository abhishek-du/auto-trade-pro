import { useState, useRef, useEffect } from 'react'
import { Search, Plus, Trash2, RefreshCw, Loader2 } from 'lucide-react'

function fmtINR(n) {
  if (n == null) return '—'
  return `₹${(+n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function CategoryTag({ cat }) {
  const colors = {
    'ELSS':        'bg-purple-500/15 text-purple-400',
    'Index':       'bg-blue-500/15 text-blue-400',
    'Large Cap':   'bg-emerald-500/15 text-emerald-400',
    'Mid Cap':     'bg-amber-500/15 text-amber-400',
    'Small Cap':   'bg-red-500/15 text-red-400',
    'Hybrid':      'bg-cyan-500/15 text-cyan-400',
    'Liquid':      'bg-slate-500/15 text-slate-400',
    'Debt':        'bg-indigo-500/15 text-indigo-400',
  }
  return (
    <span className={`px-1.5 py-0.5 rounded text-[9px] font-semibold ${colors[cat] || 'bg-surface text-muted'}`}>
      {cat || 'Equity'}
    </span>
  )
}

export default function FundManager({ goalId, funds, searchFunds, addFund, removeFund }) {
  const [query,    setQuery]    = useState('')
  const [results,  setResults]  = useState([])
  const [searching,setSearching]= useState(false)
  const [showForm, setShowForm] = useState(false)
  const [selected, setSelected] = useState(null)
  const [form,     setForm]     = useState({ monthly_amount: 5000, start_date: new Date().toISOString().slice(0,10) })
  const [saving,   setSaving]   = useState(false)
  const [err,      setErr]      = useState('')
  const timerRef = useRef(null)

  const handleSearch = (v) => {
    setQuery(v)
    clearTimeout(timerRef.current)
    if (v.length < 2) { setResults([]); return }
    setSearching(true)
    timerRef.current = setTimeout(async () => {
      try {
        const data = await searchFunds(v)
        setResults(Array.isArray(data) ? data : [])
      } catch { setResults([]) }
      finally  { setSearching(false) }
    }, 350)
  }

  const handleSelect = (fund) => {
    setSelected(fund)
    setQuery(fund.scheme_name)
    setResults([])
    setShowForm(true)
    setErr('')
  }

  const handleAdd = async () => {
    if (!selected) return
    setErr('')
    if (!form.monthly_amount || +form.monthly_amount <= 0) {
      setErr('Enter a valid monthly amount')
      return
    }
    setSaving(true)
    try {
      await addFund(goalId, {
        scheme_code:    selected.scheme_code,
        scheme_name:    selected.scheme_name,
        fund_house:     selected.fund_house || '',
        category:       selected.category   || '',
        monthly_amount: +form.monthly_amount,
        start_date:     form.start_date,
      })
      setQuery('')
      setSelected(null)
      setShowForm(false)
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Search box */}
      <div className="space-y-2">
        <div className="relative">
          <Search size={12} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={query}
            onChange={e => handleSearch(e.target.value)}
            placeholder="Search mutual funds to add…"
            className="w-full bg-surface border border-border rounded-lg pl-8 pr-4 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
          />
          {searching && <Loader2 size={12} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted animate-spin" />}
        </div>

        {results.length > 0 && (
          <div className="rounded-lg border border-border overflow-hidden shadow-xl" style={{ background: '#0a0f1c' }}>
            {results.map(r => (
              <button
                key={r.scheme_code}
                onClick={() => handleSelect(r)}
                className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-accent/10 transition-colors text-left"
              >
                <div>
                  <p className="text-slate-200 text-xs font-medium truncate max-w-[260px]">{r.scheme_name}</p>
                  <p className="text-muted text-[10px] mt-0.5">{r.scheme_code}</p>
                </div>
                <CategoryTag cat={r.category} />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Add form */}
      {showForm && selected && (
        <div className="rounded-xl border border-accent/30 p-4 space-y-3" style={{ background: '#0d1829' }}>
          <div>
            <p className="text-slate-100 font-semibold text-sm truncate">{selected.scheme_name}</p>
            <div className="flex items-center gap-2 mt-0.5">
              <CategoryTag cat={selected.category} />
              {selected.nav && <span className="text-muted text-[10px]">NAV: ₹{selected.nav?.toFixed(2)}</span>}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">Monthly Amount (₹)</label>
              <input
                type="number" min="100" step="500" value={form.monthly_amount}
                onChange={e => setForm(p => ({ ...p, monthly_amount: e.target.value }))}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
            <div className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">Start Date</label>
              <input
                type="date" value={form.start_date}
                onChange={e => setForm(p => ({ ...p, start_date: e.target.value }))}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
          </div>
          {err && <p className="text-loss text-xs">{err}</p>}
          <div className="flex gap-2">
            <button
              onClick={handleAdd} disabled={saving}
              className="px-4 py-2 bg-accent hover:bg-accent/90 disabled:opacity-50 text-white rounded-lg text-xs font-semibold transition-colors"
            >
              {saving ? 'Adding…' : 'Add Fund'}
            </button>
            <button onClick={() => { setShowForm(false); setSelected(null); setQuery('') }}
              className="px-4 py-2 bg-surface hover:bg-white/5 text-muted rounded-lg text-xs transition-colors">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Fund list */}
      {funds.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-28 gap-1">
          <p className="text-muted text-sm">No funds linked to this goal</p>
          <p className="text-muted/50 text-xs">Search and add a fund above</p>
        </div>
      ) : (
        <div className="space-y-2">
          {funds.map(f => (
            <div key={f.id} className="flex items-center justify-between rounded-xl border border-border px-4 py-3" style={{ background: '#0a0f1c' }}>
              <div className="min-w-0">
                <p className="text-slate-200 text-xs font-medium truncate">{f.scheme_name}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  <CategoryTag cat={f.category} />
                  <span className="text-muted text-[10px]">from {f.start_date}</span>
                </div>
              </div>
              <div className="flex items-center gap-3 flex-shrink-0 ml-3">
                <div className="text-right">
                  <p className="text-accent font-semibold text-sm tabular-nums">{fmtINR(f.monthly_amount)}/mo</p>
                  <p className={`text-[10px] ${f.is_active ? 'text-profit' : 'text-muted'}`}>
                    {f.is_active ? 'Active' : 'Inactive'}
                  </p>
                </div>
                <button
                  onClick={() => removeFund(goalId, f.id)}
                  className="p-1.5 rounded-lg text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
