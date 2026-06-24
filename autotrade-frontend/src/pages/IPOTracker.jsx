import { useState, useMemo } from 'react'
import { TrendingUp, RefreshCw, BarChart2, Key, Clock } from 'lucide-react'
import { useIPOTracker } from '../hooks/useIPOTracker'
import IPOCard from '../components/ipo/IPOCard'
import IPODetailPanel from '../components/ipo/IPODetailPanel'
import LoadingSpinner from '../components/LoadingSpinner'

function NoIPOsState({ activeTab, hasAnyData, apiKeyConfigured, onRefresh }) {
  if (!hasAnyData) {
    if (apiKeyConfigured) {
      // Key is set but data is empty — likely rate-limited on first load
      return (
        <div className="rounded-xl border border-blue-500/20 px-6 py-10 flex flex-col items-center gap-4 text-center" style={{ background: 'rgba(59,130,246,0.04)' }}>
          <div className="w-14 h-14 rounded-full border-2 border-dashed border-blue-500/30 flex items-center justify-center">
            <Clock size={22} className="text-blue-400/60" />
          </div>
          <div className="space-y-1">
            <p className="text-slate-200 font-semibold text-sm">IPO data is loading</p>
            <p className="text-muted text-xs max-w-xs">
              ipoalerts.in free plan fetches ~6 IPOs per request window.
              Cache refreshes every 30 minutes — data accumulates across cycles.
            </p>
          </div>
          <button onClick={onRefresh} className="text-xs text-muted hover:text-slate-300 transition-colors flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border hover:border-accent/40">
            <RefreshCw size={11} /> Refresh now
          </button>
        </div>
      )
    }
    return (
      <div className="rounded-xl border border-amber-500/20 px-6 py-10 flex flex-col items-center gap-4 text-center" style={{ background: 'rgba(245,158,11,0.04)' }}>
        <div className="w-14 h-14 rounded-full border-2 border-dashed border-amber-500/30 flex items-center justify-center">
          <Key size={22} className="text-amber-400/60" />
        </div>
        <div className="space-y-1">
          <p className="text-slate-200 font-semibold text-sm">IPO data needs an API key</p>
          <p className="text-muted text-xs max-w-xs">
            NSE's API requires browser JS execution (Akamai) — not accessible from a server.
            ipoalerts.in is the reliable data source.
          </p>
        </div>
        <div className="rounded-xl border border-border px-5 py-3 text-left space-y-2 w-full max-w-sm" style={{ background: '#0a0f1c' }}>
          <p className="text-muted text-[10px] uppercase tracking-widest">Setup (2 steps)</p>
          <p className="text-slate-300 text-xs">
            1. Get a free API key at{' '}
            <span className="text-accent font-mono">ipoalerts.in</span>
          </p>
          <p className="text-slate-300 text-xs">
            2. Add to <code className="bg-surface px-1.5 py-0.5 rounded text-amber-400">.env</code>:
          </p>
          <code className="block bg-surface rounded px-3 py-2 text-[11px] text-green-400 font-mono select-all">
            IPOALERTS_API_KEY=your_key_here
          </code>
          <p className="text-muted/60 text-[10px]">Then restart the backend — data loads automatically.</p>
        </div>
        <button onClick={onRefresh} className="text-xs text-muted hover:text-slate-300 transition-colors flex items-center gap-1">
          <RefreshCw size={11} /> Retry anyway
        </button>
      </div>
    )
  }
  return (
    <div className="rounded-xl border border-border px-5 py-10 flex flex-col items-center gap-2 glass-panel">
      <BarChart2 size={24} className="text-muted/30" />
      <p className="text-muted text-sm">No {activeTab} IPOs right now</p>
      <p className="text-muted/50 text-xs">Switch tabs or check back soon</p>
    </div>
  )
}

const TABS = [
  { id: 'open',      label: 'Open',      desc: 'Live subscriptions' },
  { id: 'upcoming',  label: 'Upcoming',  desc: 'Opening soon' },
  { id: 'listed',    label: 'Recently Listed', desc: 'Post-listing' },
  { id: 'announced', label: 'Announced', desc: 'DRHP filed' },
]

const TYPE_FILTERS = [
  { id: null,    label: 'All'       },
  { id: 'EQ',    label: 'Mainboard' },
  { id: 'SME',   label: 'SME'       },
  { id: 'DEBT',  label: 'Debt'      },
]

function StatPill({ label, value, color }) {
  return (
    <div className="flex flex-col items-center px-4 py-2 rounded-xl border border-border glass-panel">
      <span className="text-lg font-black tabular-nums" style={{ color }}>{value}</span>
      <span className="text-[10px] text-muted uppercase tracking-widest">{label}</span>
    </div>
  )
}

export default function IPOTracker() {
  const { ipos, loading, error, stats, cachedAt, dataSource, fetchIpos, refresh, fetchAnalysis } = useIPOTracker()

  const [activeTab,   setActiveTab]   = useState('open')
  const [typeFilter,  setTypeFilter]  = useState(null)
  const [selectedIpo, setSelectedIpo] = useState(null)

  const filtered = useMemo(() => {
    const list = activeTab === 'upcoming'
      ? ipos.filter(i => i.status === 'upcoming' || i.status === 'announced')
      : ipos.filter(i => i.status === activeTab)
    return typeFilter ? list.filter(i => i.ipo_type === typeFilter) : list
  }, [ipos, activeTab, typeFilter])

  function handleTabChange(tabId) {
    setActiveTab(tabId)
    // Always fetch all statuses so switching tabs doesn't wipe other status data
    fetchIpos()
  }

  return (
    <div className="space-y-5 fade-in">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <TrendingUp size={18} className="text-cyan" />
            IPO Tracker
          </h1>
          <p className="text-muted text-sm mt-0.5">
            {dataSource === 'ipoalerts' ? 'ipoalerts.in' : dataSource === 'nse_fallback' ? 'NSE (fallback)' : 'Live Indian IPOs'}
            {cachedAt && (
              <span className="ml-2 text-muted/50 text-xs">
                Updated {new Date(cachedAt).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
            {dataSource === 'nse_fallback' && (
              <span className="ml-2 text-amber-400/70 text-xs">· Set IPOALERTS_API_KEY in .env for full data + GMP</span>
            )}
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-muted text-xs hover:text-slate-300 hover:border-accent/40 transition-colors"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {/* Stats row */}
      {stats && (
        <div className="flex items-center gap-3 flex-wrap">
          <StatPill label="Total"     value={stats.total}                      color="#94A3B8" />
          <StatPill label="Open"      value={stats.by_status?.open      ?? 0}  color="#10B981" />
          <StatPill label="Upcoming"  value={stats.by_status?.upcoming  ?? 0}  color="#3B82F6" />
          <StatPill label="Announced" value={stats.by_status?.announced ?? 0}  color="#8B5CF6" />
          <StatPill label="Listed"    value={stats.by_status?.listed    ?? 0}  color="#64748B" />
        </div>
      )}

      <div className="flex gap-5">
        {/* Left: list */}
        <div className="flex-1 min-w-0 space-y-4">
          {/* Tabs */}
          <div className="flex items-center gap-0.5 glass-panel border border-border rounded-xl p-1 w-fit max-w-full overflow-x-auto scrollbar-none">
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => handleTabChange(t.id)}
                className={`flex items-center px-3 py-1.5 rounded-lg text-xs font-semibold shrink-0 whitespace-nowrap transition-colors ${activeTab === t.id ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'}`}
              >
                {t.label}
                {stats?.by_status?.[t.id] > 0 && (
                  <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded-full bg-surface">{stats.by_status[t.id]}</span>
                )}
              </button>
            ))}
          </div>

          {/* Type filter */}
          <div className="flex items-center gap-1.5">
            {TYPE_FILTERS.map(f => (
              <button
                key={String(f.id)}
                onClick={() => setTypeFilter(f.id)}
                className={`px-2.5 py-1 rounded-lg text-[10px] font-semibold border transition-colors ${typeFilter === f.id ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border text-muted hover:text-slate-300'}`}
              >
                {f.label}
              </button>
            ))}
          </div>

          {/* Content */}
          {loading ? (
            <LoadingSpinner message="Fetching IPO data…" />
          ) : error ? (
            <div className="rounded-xl border border-red-500/20 px-5 py-4 text-red-400 text-sm" style={{ background: 'rgba(239,68,68,0.04)' }}>
              {error}
            </div>
          ) : filtered.length === 0 ? (
            <NoIPOsState activeTab={activeTab} hasAnyData={stats?.total > 0} apiKeyConfigured={stats?.api_key_configured} onRefresh={refresh} />

          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {filtered.map(ipo => (
                <IPOCard
                  key={ipo.id || ipo.slug}
                  ipo={ipo}
                  onClick={setSelectedIpo}
                />
              ))}
            </div>
          )}
        </div>

        {/* Right: detail panel */}
        {selectedIpo && (
          <div
            className="w-[420px] shrink-0 rounded-2xl border border-border overflow-hidden flex flex-col"
            style={{ background: '#0F1829', maxHeight: 'calc(100vh - 9rem)', position: 'sticky', top: '1.5rem' }}
          >
            <IPODetailPanel
              ipo={selectedIpo}
              onClose={() => setSelectedIpo(null)}
              fetchAnalysis={fetchAnalysis}
            />
          </div>
        )}
      </div>
    </div>
  )
}
