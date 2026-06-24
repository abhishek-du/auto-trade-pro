import { useState, useEffect, useRef } from 'react'
import {
  Search, Plus, Trash2, RefreshCw, Brain, TrendingUp,
  TrendingDown, PauseCircle, PlayCircle, X, ChevronDown,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { useMFTracker } from '../../hooks/useMFTracker'
import { formatINR } from '../../utils/indianFormat'

// ── helpers ───────────────────────────────────────────────────────────────────

function PctBadge({ value }) {
  if (value == null) return <span className="text-muted text-xs">—</span>
  const pos = value >= 0
  return (
    <span className={`text-xs font-semibold tabular-nums ${pos ? 'text-profit' : 'text-loss'}`}>
      {pos ? '+' : ''}{(+value).toFixed(1)}%
    </span>
  )
}

function CategoryTag({ label }) {
  const colors = {
    'ELSS':       'bg-purple-500/15 text-purple-300',
    'Index':      'bg-cyan/15 text-cyan',
    'Mid Cap':    'bg-amber-500/15 text-amber-300',
    'Large Cap':  'bg-blue-500/15 text-blue-300',
    'Small Cap':  'bg-orange-500/15 text-orange-300',
    'Hybrid':     'bg-teal-500/15 text-teal-300',
    'Liquid':     'bg-slate-400/15 text-slate-300',
    'Debt':       'bg-green-500/15 text-green-300',
    'Equity':     'bg-indigo-500/15 text-indigo-300',
  }
  return (
    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${colors[label] || 'bg-white/10 text-muted'}`}>
      {label}
    </span>
  )
}

// ── Fund search box ───────────────────────────────────────────────────────────

function FundSearchBox({ onAdd }) {
  const [query,     setQuery]     = useState('')
  const [results,   setResults]   = useState([])
  const [searching, setSearching] = useState(false)
  const [adding,    setAdding]    = useState(false)
  const debounceRef = useRef(null)
  const { searchFunds, addFund } = useMFTracker()

  useEffect(() => {
    if (query.trim().length < 2) { setResults([]); return }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try { setResults(await searchFunds(query)) }
      catch { setResults([]) }
      finally { setSearching(false) }
    }, 350)
    return () => clearTimeout(debounceRef.current)
  }, [query])

  async function handleAdd(fund) {
    setAdding(true)
    try {
      await addFund({ scheme_code: fund.scheme_code, scheme_name: fund.scheme_name, category: fund.category })
      toast.success(`${fund.scheme_name.slice(0, 40)}… added`)
      setQuery('')
      setResults([])
      onAdd?.()
    } catch (err) {
      toast.error(err.message || 'Failed to add fund')
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-2 p-3 rounded-xl border border-border bg-bg">
        <Search size={14} className="text-muted shrink-0" />
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search mutual funds — e.g. HDFC, SBI Bluechip, Nifty 50…"
          className="flex-1 bg-transparent text-sm text-slate-200 placeholder-muted outline-none"
        />
        {searching && <div className="w-3.5 h-3.5 border border-muted border-t-transparent rounded-full animate-spin shrink-0" />}
        {query && !searching && (
          <button onClick={() => { setQuery(''); setResults([]) }} className="text-muted hover:text-white">
            <X size={13} />
          </button>
        )}
      </div>

      {results.length > 0 && (
        <div className="absolute top-full mt-1 left-0 right-0 z-50 glass-panel border border-border rounded-xl shadow-2xl overflow-hidden max-h-72 overflow-y-auto">
          {results.map(r => (
            <button
              key={r.scheme_code}
              onClick={() => handleAdd(r)}
              disabled={adding}
              className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-white/5 transition-colors text-left disabled:opacity-50"
            >
              <div className="flex-1 min-w-0">
                <p className="text-slate-200 text-sm font-medium truncate">{r.scheme_name}</p>
                <p className="text-muted text-[10px] mt-0.5">Code: {r.scheme_code}</p>
              </div>
              <div className="flex items-center gap-2 ml-3 shrink-0">
                <CategoryTag label={r.category} />
                <Plus size={13} className="text-accent" />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Fund card ─────────────────────────────────────────────────────────────────

function FundCard({ fund, onRemove, onRefresh, onAddSip }) {
  const [refreshing, setRefreshing] = useState(false)

  async function handleRefresh() {
    setRefreshing(true)
    try {
      await onRefresh(fund.id)
      toast.success('NAV refreshed')
    } catch { toast.error('Refresh failed') }
    finally { setRefreshing(false) }
  }

  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-slate-100 font-semibold text-sm leading-snug line-clamp-2">{fund.scheme_name}</p>
          <div className="flex items-center gap-2 mt-1.5">
            <CategoryTag label={fund.category} />
            <span className="text-muted text-[10px]">{fund.scheme_code}</span>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={handleRefresh} title="Refresh NAV" className="p-1.5 rounded text-muted hover:text-white hover:bg-white/5 transition-colors">
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
          </button>
          <button onClick={() => onRemove(fund.id)} title="Remove" className="p-1.5 rounded text-muted hover:text-loss hover:bg-loss/10 transition-colors">
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <div>
          <p className="text-muted text-[9px] uppercase tracking-wider mb-0.5">NAV</p>
          <p className="text-slate-100 font-bold text-sm tabular-nums">
            {fund.nav ? `₹${(+fund.nav).toFixed(2)}` : '—'}
          </p>
        </div>
        <div>
          <p className="text-muted text-[9px] uppercase tracking-wider mb-0.5">1M</p>
          <PctBadge value={fund.one_month_return} />
        </div>
        <div>
          <p className="text-muted text-[9px] uppercase tracking-wider mb-0.5">1Y</p>
          <PctBadge value={fund.one_year_return} />
        </div>
        <div>
          <p className="text-muted text-[9px] uppercase tracking-wider mb-0.5">3Y</p>
          <PctBadge value={fund.three_year_return} />
        </div>
      </div>

      <div className="flex items-center justify-between pt-1 border-t border-border/50">
        <div className="text-[11px] text-muted">
          {fund.sip_count > 0
            ? <span className="text-accent font-semibold">{fund.sip_count} active SIP{fund.sip_count > 1 ? 's' : ''} · ₹{(+fund.total_monthly_sip).toLocaleString('en-IN')}/mo</span>
            : 'No SIP running'}
        </div>
        <button
          onClick={() => onAddSip(fund)}
          className="flex items-center gap-1 text-[11px] text-accent hover:text-cyan font-semibold transition-colors"
        >
          <Plus size={11} /> Add SIP
        </button>
      </div>
    </div>
  )
}

// ── Add SIP modal ─────────────────────────────────────────────────────────────

function AddSIPModal({ fund, onClose, onSave }) {
  const [amount,    setAmount]    = useState('500')
  const [startDate, setStartDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [notes,     setNotes]     = useState('')
  const [saving,    setSaving]    = useState(false)

  const months = Math.max(1, Math.round((new Date() - new Date(startDate)) / (1000 * 60 * 60 * 24 * 30)))
  const totalInvested = parseFloat(amount || 0) * months
  const projected = amount ? (() => {
    const r = Math.pow(1.12, 1 / 12) - 1
    return r === 0 ? parseFloat(amount) * months : parseFloat(amount) * ((Math.pow(1 + r, months) - 1) / r) * (1 + r)
  })() : 0

  async function handleSave(e) {
    e.preventDefault()
    if (!amount || parseFloat(amount) <= 0) { toast.error('Enter a valid amount'); return }
    setSaving(true)
    try {
      await onSave({ fund_id: fund.id, monthly_amount: parseFloat(amount), start_date: startDate, notes })
      toast.success('SIP added')
      onClose()
    } catch (err) { toast.error(err.message || 'Failed to add SIP') }
    finally { setSaving(false) }
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <div className="fixed right-0 top-0 h-full w-full max-w-sm z-50 flex flex-col glass-panel border-l border-border shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="text-slate-100 font-bold text-sm">Add SIP</h2>
          <button onClick={onClose} className="p-1.5 rounded text-muted hover:text-white hover:bg-white/10">
            <X size={15} />
          </button>
        </div>

        <form onSubmit={handleSave} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          <div className="p-3 rounded-lg bg-accent/5 border border-accent/20">
            <p className="text-slate-100 text-sm font-semibold line-clamp-2">{fund.scheme_name}</p>
            <CategoryTag label={fund.category} />
          </div>

          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Monthly Amount (₹)</label>
            <input
              autoFocus type="number" min="100" step="100"
              value={amount} onChange={e => setAmount(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 outline-none focus:border-accent/50"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">SIP Start Date</label>
            <input
              type="date" value={startDate}
              onChange={e => setStartDate(e.target.value)}
              max={new Date().toISOString().slice(0, 10)}
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 outline-none focus:border-accent/50"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Notes (optional)</label>
            <input
              value={notes} onChange={e => setNotes(e.target.value)}
              placeholder="e.g. Long term, retirement fund…"
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
            />
          </div>

          {amount && parseFloat(amount) > 0 && (
            <div className="grid grid-cols-2 gap-3 p-3 rounded-lg bg-white/3 border border-border/50">
              <div>
                <p className="text-muted text-[10px] uppercase tracking-wider mb-1">Invested so far</p>
                <p className="text-slate-100 font-bold text-sm">₹{totalInvested.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
              </div>
              <div>
                <p className="text-muted text-[10px] uppercase tracking-wider mb-1">Projected @12%</p>
                <p className="text-profit font-bold text-sm">₹{projected.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
              </div>
            </div>
          )}
        </form>

        <div className="px-5 py-4 border-t border-border flex gap-3">
          <button type="button" onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-border text-sm text-muted hover:text-white transition-colors">Cancel</button>
          <button onClick={handleSave} disabled={saving} className="flex-1 py-2.5 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50">
            {saving ? 'Saving…' : 'Add SIP'}
          </button>
        </div>
      </div>
    </>
  )
}

// ── SIP row ───────────────────────────────────────────────────────────────────

function SIPRow({ sip, onToggle, onDelete }) {
  const isActive = sip.status === 'active'
  const gain = sip.projected_value - sip.total_invested

  return (
    <tr className="hover:bg-white/[0.02] transition-colors">
      <td className="px-4 py-3">
        <p className="text-slate-200 text-sm font-semibold truncate max-w-[200px]">{sip.scheme_name}</p>
        <CategoryTag label={sip.category} />
      </td>
      <td className="px-4 py-3 text-slate-300 tabular-nums text-sm font-semibold">
        ₹{sip.monthly_amount.toLocaleString('en-IN')}
      </td>
      <td className="px-4 py-3 text-muted text-xs">{sip.start_date}</td>
      <td className="px-4 py-3 text-slate-300 tabular-nums text-xs">{sip.months_invested}m</td>
      <td className="px-4 py-3 text-slate-300 tabular-nums text-xs">
        ₹{sip.total_invested.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
      </td>
      <td className="px-4 py-3 tabular-nums text-xs">
        <p className="text-profit font-semibold">₹{sip.projected_value.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
        <p className={`text-[10px] ${gain >= 0 ? 'text-profit/70' : 'text-loss/70'}`}>
          {gain >= 0 ? '+' : ''}₹{Math.abs(gain).toLocaleString('en-IN', { maximumFractionDigits: 0 })} est.
        </p>
      </td>
      <td className="px-4 py-3">
        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${isActive ? 'bg-profit/15 text-profit' : 'bg-muted/15 text-muted'}`}>
          {sip.status}
        </span>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1">
          <button
            onClick={() => onToggle(sip.id, isActive ? 'paused' : 'active')}
            title={isActive ? 'Pause SIP' : 'Resume SIP'}
            className="p-1.5 rounded text-muted hover:text-accent hover:bg-accent/10 transition-colors"
          >
            {isActive ? <PauseCircle size={13} /> : <PlayCircle size={13} />}
          </button>
          <button
            onClick={() => onDelete(sip.id)}
            title="Delete SIP"
            className="p-1.5 rounded text-muted hover:text-loss hover:bg-loss/10 transition-colors"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </td>
    </tr>
  )
}

// ── AI Analysis panel ─────────────────────────────────────────────────────────

function AnalysisPanel({ analysis, analyzing, onRun }) {
  return (
    <div className="rounded-xl border border-border glass-panel overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
        <div className="flex items-center gap-2">
          <Brain size={14} className="text-accent" />
          <h3 className="text-slate-100 font-semibold text-sm">AI Portfolio Analysis</h3>
        </div>
        <button
          onClick={onRun}
          disabled={analyzing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-accent text-white text-xs font-semibold disabled:opacity-50 hover:opacity-90 transition-opacity"
        >
          <Brain size={11} />
          {analyzing ? 'Analysing…' : 'Analyse Portfolio'}
        </button>
      </div>

      <div className="px-5 py-4">
        {analyzing && (
          <div className="flex items-center gap-3 text-muted text-sm">
            <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            Groq AI is analysing your portfolio…
          </div>
        )}
        {!analyzing && !analysis && (
          <p className="text-muted text-sm text-center py-4">
            Click "Analyse Portfolio" to get AI insights on your funds and SIPs.
          </p>
        )}
        {!analyzing && analysis && (
          <div className="space-y-3">
            {analysis.total_monthly_sip > 0 && (
              <div className="flex items-center gap-4 p-3 rounded-lg bg-accent/5 border border-accent/15">
                <div className="text-center">
                  <p className="text-[10px] text-muted uppercase tracking-wider">Funds</p>
                  <p className="text-slate-100 font-bold">{analysis.fund_count}</p>
                </div>
                <div className="text-center">
                  <p className="text-[10px] text-muted uppercase tracking-wider">SIPs</p>
                  <p className="text-slate-100 font-bold">{analysis.sip_count}</p>
                </div>
                <div className="text-center">
                  <p className="text-[10px] text-muted uppercase tracking-wider">Monthly</p>
                  <p className="text-profit font-bold">₹{analysis.total_monthly_sip.toLocaleString('en-IN')}</p>
                </div>
              </div>
            )}
            <div className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
              {analysis.analysis}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function MyMutualFunds() {
  const {
    funds, sips, loading, sipLoading, analysis, analyzing,
    loadFunds, loadSips,
    removeFund, refreshFundNav,
    addSip, updateSip, deleteSip,
    runAnalysis,
  } = useMFTracker()

  const [sipTarget, setSipTarget] = useState(null)

  useEffect(() => { loadFunds(); loadSips() }, [])

  async function handleRemoveFund(id) {
    if (!confirm('Remove this fund? Associated SIPs will also be deleted.')) return
    try { await removeFund(id); toast.success('Fund removed') }
    catch { toast.error('Failed to remove fund') }
  }

  async function handleToggleSip(id, status) {
    try {
      await updateSip(id, { status })
      toast.success(status === 'active' ? 'SIP resumed' : 'SIP paused')
    } catch { toast.error('Failed to update SIP') }
  }

  async function handleDeleteSip(id) {
    if (!confirm('Delete this SIP entry?')) return
    try { await deleteSip(id); toast.success('SIP deleted') }
    catch { toast.error('Failed to delete SIP') }
  }

  const totalMonthly = sips.filter(s => s.status === 'active').reduce((sum, s) => sum + s.monthly_amount, 0)
  const totalInvested = sips.reduce((sum, s) => sum + s.total_invested, 0)
  const totalProjected = sips.reduce((sum, s) => sum + s.projected_value, 0)

  return (
    <div className="space-y-5">
      {/* ── Summary strip ── */}
      {(funds.length > 0 || sips.length > 0) && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { label: 'Funds Tracked', value: funds.length, fmt: v => v },
            { label: 'Active SIPs',   value: sips.filter(s => s.status === 'active').length, fmt: v => v },
            { label: 'Monthly SIP',   value: totalMonthly,  fmt: v => `₹${v.toLocaleString('en-IN')}` },
            { label: 'Total Invested', value: totalInvested, fmt: v => `₹${Math.round(v).toLocaleString('en-IN')}` },
          ].map(({ label, value, fmt }) => (
            <div key={label} className="glass-panel border border-border rounded-xl px-4 py-3">
              <p className="text-muted text-[10px] uppercase tracking-wider mb-1">{label}</p>
              <p className="text-slate-100 font-bold text-lg tabular-nums">{fmt(value)}</p>
            </div>
          ))}
        </div>
      )}

      {/* ── Fund search ── */}
      <div className="space-y-2">
        <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider">Add Mutual Fund</h3>
        <FundSearchBox onAdd={() => { loadFunds(); loadSips() }} />
      </div>

      {/* ── My Funds ── */}
      {loading ? (
        <div className="text-center py-8 text-muted text-sm">Loading funds…</div>
      ) : funds.length === 0 ? (
        <div className="rounded-xl border border-border border-dashed p-10 text-center space-y-2">
          <p className="text-slate-300 font-semibold">No funds added yet</p>
          <p className="text-muted text-sm">Search above to find and add mutual funds</p>
        </div>
      ) : (
        <div className="space-y-2">
          <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider">My Funds ({funds.length})</h3>
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
            {funds.map(f => (
              <FundCard
                key={f.id}
                fund={f}
                onRemove={handleRemoveFund}
                onRefresh={refreshFundNav}
                onAddSip={setSipTarget}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── SIPs table ── */}
      {sips.length > 0 && (
        <div className="rounded-xl border border-border overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
            <h3 className="text-slate-100 font-semibold text-sm">My SIPs</h3>
            <div className="text-muted text-xs">
              {sips.filter(s => s.status === 'active').length} active ·
              Projected corpus: <span className="text-profit font-semibold">₹{Math.round(totalProjected).toLocaleString('en-IN')}</span>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  {['Fund', 'Monthly', 'Since', 'Duration', 'Invested', 'Projected @12%', 'Status', ''].map(h => (
                    <th key={h} className="text-left px-4 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {sips.map(sip => (
                  <SIPRow
                    key={sip.id}
                    sip={sip}
                    onToggle={handleToggleSip}
                    onDelete={handleDeleteSip}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── AI Analysis ── */}
      <AnalysisPanel analysis={analysis} analyzing={analyzing} onRun={runAnalysis} />

      {/* ── Add SIP modal ── */}
      {sipTarget && (
        <AddSIPModal
          fund={sipTarget}
          onClose={() => setSipTarget(null)}
          onSave={addSip}
        />
      )}
    </div>
  )
}
