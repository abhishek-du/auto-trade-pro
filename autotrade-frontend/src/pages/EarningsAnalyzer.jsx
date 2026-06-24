import { useState, useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { FileText, RefreshCw, ExternalLink, GitCompare, X, Search, Sparkles } from 'lucide-react'
import { useEarnings } from '../hooks/useEarnings'
import ToneIndicator   from '../components/earnings/ToneIndicator'
import SummarySection  from '../components/earnings/SummarySection'
import GuidanceCards   from '../components/earnings/GuidanceCards'
import QuarterSelector from '../components/earnings/QuarterSelector'
import ComparisonView  from '../components/earnings/ComparisonView'
import EarningsCard    from '../components/earnings/EarningsCard'
import { apiFetch } from '../api/client'

const QUICK_STOCKS = ['INFY.NS', 'TCS.NS', 'HDFCBANK.NS', 'RELIANCE.NS', 'WIPRO.NS']

// ── Loading skeleton ──────────────────────────────────────────────────────────

function LoadingSkeleton({ progressMsg }) {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-5 w-64 bg-border rounded" />
      <div className="grid grid-cols-4 gap-3">
        {[0,1,2,3].map(i => <div key={i} className="h-16 bg-border rounded-xl" />)}
      </div>
      <div className="grid grid-cols-5 gap-4">
        <div className="col-span-3 space-y-3">
          {[0,1,2].map(i => <div key={i} className="h-24 bg-border rounded-xl" />)}
        </div>
        <div className="col-span-2 space-y-3">
          {[0,1].map(i => <div key={i} className="h-28 bg-border rounded-xl" />)}
        </div>
      </div>
      {progressMsg && (
        <div className="rounded-xl border border-accent/20 bg-accent/5 px-4 py-3 flex items-center gap-3">
          <div className="w-4 h-4 border-2 border-cyan border-t-transparent rounded-full animate-spin shrink-0" />
          <p className="text-cyan text-sm">{progressMsg}</p>
          <p className="text-muted text-xs ml-auto">Usually takes 20–30 seconds</p>
        </div>
      )}
    </div>
  )
}

// ── Stock search ──────────────────────────────────────────────────────────────

function StockSearch({ onSelect }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!q.trim() || q.length < 2) { setResults([]); return }
    const timer = setTimeout(async () => {
      const r = await apiFetch(`/api/v1/portfolios/search/stocks?q=${encodeURIComponent(q)}`)
      const d = r.ok ? await r.json() : []
      setResults(d.slice(0, 8))
      setOpen(true)
    }, 300)
    return () => clearTimeout(timer)
  }, [q])

  function pick(stock) {
    onSelect(stock.symbol)
    setQ(stock.name || stock.ticker)
    setOpen(false)
    setResults([])
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-bg text-sm">
        <Search size={14} className="text-muted shrink-0" />
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          onFocus={() => results.length && setOpen(true)}
          placeholder="Search NSE company..."
          className="bg-transparent text-slate-200 placeholder-muted outline-none flex-1 min-w-32"
        />
      </div>
      {open && results.length > 0 && (
        <div className="absolute top-full mt-1 left-0 right-0 z-20 glass-panel border border-border rounded-lg shadow-xl overflow-hidden max-h-48 overflow-y-auto">
          {results.map(r => (
            <div key={r.symbol} onClick={() => pick(r)}
              className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-white/5 text-xs">
              <span className="text-slate-200 font-medium">{r.name}</span>
              <span className="text-muted">{r.ticker}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Recent feed ───────────────────────────────────────────────────────────────

function RecentFeed() {
  const [recent, setRecent] = useState([])
  useEffect(() => {
    apiFetch('/api/v1/earnings/recent?limit=6')
      .then(r => r.ok ? r.json() : [])
      .then(d => setRecent(Array.isArray(d) ? d : []))
      .catch(() => {})
  }, [])
  if (!recent.length) return null
  return (
    <div className="space-y-3">
      <h3 className="text-slate-300 font-semibold text-sm">Recently Analysed</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
        {recent.map(s => <EarningsCard key={`${s.symbol}-${s.quarter}`} summary={s} />)}
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function EarningsAnalyzer() {
  const [searchParams] = useSearchParams()
  const nav            = useNavigate()
  const [symbol, setSymbol] = useState(searchParams.get('symbol') || '')
  const [compareQuarters, setCompareQuarters] = useState([])

  const {
    summary, availableList, selectedQuarter,
    loading, error, progressMsg,
    compareMode, compareSummaries,
    fetchSummary, loadComparison, refreshSummary, setCompareMode,
  } = useEarnings(symbol)

  function selectSymbol(sym) {
    setSymbol(sym)
    nav(`/earnings?symbol=${sym}`, { replace: true })
  }

  const cachedQuarters = new Set(
    availableList.filter(t => t.has_summary).map(t => t.quarter)
  )
  const allQuarters = availableList.map(t => t.quarter).filter(Boolean)

  const sourceBadge = summary?.source === 'BSE' ? 'BSE Filing' :
                      summary?.source === 'NSE' ? 'NSE Disclosure' : 'Trendlyne'

  return (
    <div className="space-y-6 fade-in">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl" style={{ background: 'rgba(59,130,246,0.12)' }}>
            <FileText size={20} className="text-blue-400" />
          </div>
          <div>
            <h1 className="text-slate-100 font-bold text-xl">Earnings Call Analyzer</h1>
            <p className="text-muted text-sm">AI-powered BSE/NSE transcript analysis</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <StockSearch onSelect={selectSymbol} />
          {symbol && summary && (
            <>
              <button
                onClick={() => refreshSummary(selectedQuarter)}
                disabled={loading}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-muted text-xs hover:text-white hover:bg-white/5 transition-all disabled:opacity-50"
              >
                <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
              </button>
              {summary.pdf_url && (
                <a href={summary.pdf_url} target="_blank" rel="noopener noreferrer"
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-muted text-xs hover:text-white hover:bg-white/5 transition-all">
                  <ExternalLink size={12} /> View PDF
                </a>
              )}
              {allQuarters.length > 1 && (
                <button
                  onClick={() => {
                    const qs = allQuarters.slice(0, 3)
                    setCompareQuarters(qs)
                    loadComparison(qs)
                  }}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-accent/30 text-cyan text-xs hover:bg-accent/10 transition-all"
                >
                  <GitCompare size={12} /> Compare Quarters
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Error */}
      {error && !loading && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/8 px-5 py-4 space-y-2">
          <div className="flex items-center gap-2">
            <FileText size={15} className="text-red-400" />
            <p className="text-red-300 text-sm font-semibold">Could not fetch transcript for {symbol}</p>
          </div>
          <p className="text-red-300/70 text-xs">{error}</p>
          <div className="text-muted text-xs space-y-0.5">
            <p>Possible reasons:</p>
            <p>• Transcript not yet filed with BSE/NSE</p>
            <p>• PDF extraction failed (may be a scanned image)</p>
            <p>• Network error — please retry</p>
          </div>
          <button onClick={() => fetchSummary(selectedQuarter, false)}
            className="text-xs px-3 py-1.5 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10">
            Retry
          </button>
        </div>
      )}

      {/* Empty / welcome state */}
      {!symbol && !loading && (
        <div className="space-y-6">
          <div className="rounded-2xl border border-border py-14 flex flex-col items-center gap-4 glass-panel">
            <div className="p-4 rounded-2xl" style={{ background: 'rgba(59,130,246,0.08)' }}>
              <Sparkles size={40} className="text-blue-400/60" />
            </div>
            <div className="text-center">
              <p className="text-slate-200 font-bold text-lg">AI Earnings Call Analyzer</p>
              <p className="text-muted text-sm mt-1 max-w-md">
                Select any NSE-listed company to get an instant AI summary of their latest earnings call transcript — sourced directly from BSE filings.
              </p>
            </div>
            <div className="flex flex-wrap gap-2 justify-center mt-2">
              {QUICK_STOCKS.map(sym => (
                <button key={sym} onClick={() => selectSymbol(sym)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs font-semibold text-cyan hover:bg-accent/10 transition-all">
                  {sym.replace('.NS', '')}
                </button>
              ))}
            </div>
          </div>
          <RecentFeed />
        </div>
      )}

      {/* Loading state */}
      {loading && <LoadingSkeleton progressMsg={progressMsg} />}

      {/* Results */}
      {!loading && summary && (
        <div className="space-y-5">
          {/* Quarter selector */}
          {allQuarters.length > 0 && (
            <QuarterSelector
              quarters={allQuarters}
              cachedQuarters={cachedQuarters}
              selected={selectedQuarter}
              onChange={q => fetchSummary(q)}
              loading={loading}
            />
          )}

          {/* Meta row */}
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <div className="md:col-span-3 rounded-xl border border-border p-4 space-y-2 glass-panel">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-slate-100 font-bold text-base">{summary.company_name}</h2>
                <span className="text-[9px] font-bold uppercase px-2 py-0.5 rounded border border-accent/30 text-cyan bg-accent/10">{summary.quarter}</span>
                <span className="text-[9px] font-bold uppercase px-2 py-0.5 rounded border border-border text-muted">{sourceBadge}</span>
                {summary.is_ai_generated && (
                  <span className="text-[9px] font-bold uppercase px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                    AI Analysis
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3 flex-wrap text-xs text-muted">
                {summary.call_date && <span>📅 {summary.call_date}</span>}
                {summary.word_count > 0 && <span>📄 {summary.word_count.toLocaleString()} words</span>}
                <span className={`font-semibold ${
                  summary.ai_confidence === 'HIGH' ? 'text-emerald-400' :
                  summary.ai_confidence === 'MEDIUM' ? 'text-amber-400' : 'text-muted'
                }`}>Confidence: {summary.ai_confidence}</span>
              </div>
            </div>
            <div className="md:col-span-2">
              <ToneIndicator tone={summary.management_tone} reason={summary.tone_reason} />
            </div>
          </div>

          {/* Guidance cards */}
          <GuidanceCards
            revenue_guidance={summary.revenue_guidance}
            margin_guidance={summary.margin_guidance}
            capex_guidance={summary.capex_guidance}
            dividend_info={summary.dividend_info}
          />

          {/* Two-column content */}
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <div className="md:col-span-3 space-y-4">
              <SummarySection title="Financial Highlights" emoji="📊" items={summary.financial_highlights} color="blue"   maxVisible={5} />
              <SummarySection title="Management Guidance"  emoji="🔭" items={summary.management_guidance}  color="green"  maxVisible={4} />
              <SummarySection title="Key Risks"            emoji="⚠️" items={summary.key_risks}            color="red"    maxVisible={4} />
            </div>
            <div className="md:col-span-2 space-y-4">
              <SummarySection title="Analyst Q&A"       emoji="❓" items={summary.analyst_questions} color="purple" maxVisible={3} />
              <SummarySection title="Strategic Updates" emoji="🚀" items={summary.strategic_updates} color="teal"   maxVisible={3} />

              {/* Ask AI button */}
              <div className="rounded-xl border border-accent/20 bg-accent/5 p-4 space-y-2">
                <p className="text-slate-300 text-xs font-medium">Have a question about this earnings call?</p>
                <a
                  href={`/chat?q=${encodeURIComponent(
                    `Analyse the ${summary.quarter} earnings call for ${summary.company_name}. ${(summary.financial_highlights[0] || '').slice(0, 100)}`
                  )}`}
                  className="flex items-center gap-2 text-xs text-cyan font-semibold hover:opacity-80 transition-opacity"
                >
                  Ask Avishk AI →
                </a>
              </div>
            </div>
          </div>

          {/* Comparison view */}
          {compareMode && compareSummaries.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-slate-300 font-semibold text-sm">Quarter Comparison</h3>
                <button onClick={() => setCompareMode(false)} className="flex items-center gap-1 text-xs text-muted hover:text-white">
                  <X size={12} /> Exit
                </button>
              </div>
              <ComparisonView summaries={compareSummaries} />
            </div>
          )}

          {/* Recent feed */}
          <div className="border-t border-border pt-5">
            <RecentFeed />
          </div>
        </div>
      )}
    </div>
  )
}
