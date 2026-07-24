import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Briefcase, Plus, RefreshCw, List, PieChart as PieIcon, Receipt, BarChart2, ExternalLink, Zap, Stethoscope, BrainCircuit, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '../components/LoadingSpinner'
import { usePortfolioTracker } from '../hooks/usePortfolioTracker'
import { useAgent } from '../hooks/useAgent'
import PortfolioSelector from '../components/portfolio/PortfolioSelector'
import SummaryCards from '../components/portfolio/SummaryCards'
import HoldingsTable from '../components/portfolio/HoldingsTable'
import AddHoldingModal from '../components/portfolio/AddHoldingModal'
import SellModal from '../components/portfolio/SellModal'
import AllocationCharts from '../components/portfolio/AllocationCharts'
import TransactionsTab from '../components/portfolio/TransactionsTab'
import { formatINR } from '../utils/indianFormat'
import { apiFetch, getZerodhaLoginUrl, getZerodhaStatus, getUpstoxLoginUrl, getUpstoxStatus, getUpstoxMargins, getUpstoxHoldings, autoLoginUpstox, syncUpstoxHoldings } from '../api/client'

/* ── Agent activity strip ──────────────────────────────────────────────────
   Inline panel showing the AI agent's paper-trading state alongside the
   user's real holdings. Surfaces wallet balance, open paper positions and
   the last few decisions. Visible right inside the unified Zerodha page so
   the user doesn't have to flip to /agent to see what the agent is doing.
   Flips visual mode based on settings paper_mode (PAPER vs LIVE badge). */
function AgentActivityPanel() {
  const { status, decisions, positions } = useAgent()

  if (!status) return null

  const isEnabled  = !!status.enabled
  const isPaper    = status.paper_mode !== false
  const openCount  = Array.isArray(positions) ? positions.length : 0
  const decoCount  = Array.isArray(decisions) ? decisions.length : 0
  const equity     = Number(status?.portfolio?.equity ?? 0)
  const cash       = Number(status?.portfolio?.cash ?? 0)

  // Recent decisions feed — strictly informational, capped at 5
  const recent = (decisions || []).slice(0, 5)

  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <BrainCircuit size={16} className="text-accent" />
          <h3 className="text-slate-100 font-semibold text-sm">AI Agent</h3>
          <span
            className={[
              'text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wide border',
              isPaper
                ? 'bg-blue-500/15 text-blue-300 border-blue-500/30'
                : 'bg-red-500/15 text-red-300 border-red-500/30 animate-pulse',
            ].join(' ')}
          >
            {isPaper ? 'Paper' : 'LIVE'}
          </span>
          <span
            className={[
              'text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wide border',
              isEnabled
                ? 'bg-profit/15 text-profit border-profit/30'
                : 'bg-surface/60 text-muted border-border',
            ].join(' ')}
          >
            {isEnabled ? 'Enabled' : 'Disabled'}
          </span>
        </div>
        <Link
          to="/agent"
          className="text-xs text-accent hover:text-accent/80 inline-flex items-center gap-1"
        >
          Open Agent <ExternalLink size={11} />
        </Link>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <div>
          <div className="text-muted text-[10px] uppercase tracking-wider">Equity</div>
          <div className="text-slate-100 font-bold tabular-nums">{formatINR(equity)}</div>
        </div>
        <div>
          <div className="text-muted text-[10px] uppercase tracking-wider">Cash</div>
          <div className="text-slate-100 font-bold tabular-nums">{formatINR(cash)}</div>
        </div>
        <div>
          <div className="text-muted text-[10px] uppercase tracking-wider">Open positions</div>
          <div className="text-slate-100 font-bold tabular-nums">{openCount}</div>
        </div>
        <div>
          <div className="text-muted text-[10px] uppercase tracking-wider">Decisions (24h)</div>
          <div className="text-slate-100 font-bold tabular-nums">{decoCount}</div>
        </div>
      </div>

      {!isPaper && (
        <div className="flex items-center gap-2 text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
          <AlertTriangle size={13} />
          Live mode: the agent's next BUY will hit your real Zerodha account. Confirm before leaving this page.
        </div>
      )}

      {recent.length > 0 && (
        <div>
          <div className="text-muted text-[10px] uppercase tracking-wider mb-1.5">Recent decisions</div>
          <ul className="space-y-1">
            {recent.map((d, i) => {
              const action = (d.action || '').toUpperCase()
              const actionCls = action === 'BUY'
                ? 'text-profit' : action === 'SELL' ? 'text-loss' : 'text-muted'
              return (
                <li key={d.id ?? i} className="text-xs flex items-center gap-2 flex-wrap">
                  <span className={`font-bold w-12 shrink-0 ${actionCls}`}>{action || '—'}</span>
                  <span className="text-slate-200 font-mono text-[11px]">{d.symbol}</span>
                  {d.confidence != null && (
                    <span className="text-muted text-[10px]">{Number(d.confidence).toFixed(0)}% conf</span>
                  )}
                  {d.strategy && (
                    <span className="text-muted text-[10px] truncate">· {d.strategy}</span>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

/* Full paper portfolio detail — equity/cash/P&L + ALL open positions with live
   P&L. This is the user's actual portfolio (the agent manages it). */
function PaperPortfolioPanel() {
  const { status } = useAgent()
  const [positions, setPositions] = useState([])

  useEffect(() => {
    const load = () => apiFetch('/api/v1/portfolio/positions').then(setPositions).catch(() => {})
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [])

  const p          = status?.portfolio
  if (!p) return null
  const equity     = Number(p.equity ?? 0)
  const cash       = Number(p.cash ?? 0)
  const deployed   = equity - cash
  const realised   = Number(p.realised_pnl ?? 0)
  const unrealised = positions.reduce((s, x) => s + (x.unrealised_pnl ?? 0), 0) || Number(p.unrealised_pnl ?? 0)
  const totalPnl   = realised + unrealised
  const start      = Number(p.start_capital ?? 2000000)
  const roi        = start > 0 ? ((equity - start) / start) * 100 : 0
  const fmt = (n) => formatINR(n ?? 0)

  const cards = [
    { label: 'Portfolio Value', value: fmt(equity), color: 'text-cyan' },
    { label: 'Free Cash',       value: fmt(cash),   sub: `${(cash / equity * 100).toFixed(0)}% buffer` },
    { label: 'Deployed',        value: fmt(deployed), sub: `${positions.length} positions` },
    { label: 'Total P&L',       value: `${totalPnl >= 0 ? '+' : ''}${fmt(totalPnl)}`, color: totalPnl >= 0 ? 'text-profit' : 'text-loss', sub: `realised ${fmt(realised)} · unreal ${fmt(unrealised)}` },
    { label: 'Return',          value: `${roi >= 0 ? '+' : ''}${roi.toFixed(2)}%`, color: roi >= 0 ? 'text-profit' : 'text-loss' },
  ]

  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Briefcase size={16} className="text-cyan" />
        <h3 className="text-slate-100 font-semibold text-sm">My Portfolio</h3>
        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/30 uppercase">Paper</span>
        <span className="text-[10px] text-muted">agent-managed · live P&amp;L</span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {cards.map((c) => (
          <div key={c.label} className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
            <div className="text-muted text-[10px] uppercase tracking-wider">{c.label}</div>
            <div className={`text-sm font-bold tabular-nums ${c.color ?? 'text-slate-100'}`}>{c.value}</div>
            {c.sub && <div className="text-[9px] text-muted truncate">{c.sub}</div>}
          </div>
        ))}
      </div>

      {positions.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted uppercase tracking-wider border-b border-border">
                {['Symbol', 'Qty', 'Entry', 'Current', 'Invested', 'P&L', 'P&L %'].map((h) => (
                  <th key={h} className="text-left px-2 py-2 font-semibold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((x, i) => {
                const pnl = x.unrealised_pnl ?? 0
                const gain = pnl >= 0
                const sym = (x.symbol ?? '').replace('.NS', '')
                return (
                  <tr key={i} className="border-b border-border/40 hover:bg-surface/30">
                    <td className="px-2 py-1.5 font-medium text-slate-200">{sym}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-300">{Number(x.size_units ?? 0).toFixed(x.size_units % 1 ? 1 : 0)}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-400">{fmt(x.entry_price)}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-100">{fmt(x.current_price)}</td>
                    <td className="px-2 py-1.5 tabular-nums text-muted">{fmt(x.size_usd)}</td>
                    <td className={`px-2 py-1.5 tabular-nums font-semibold ${gain ? 'text-profit' : 'text-loss'}`}>{gain ? '+' : ''}{fmt(pnl)}</td>
                    <td className={`px-2 py-1.5 tabular-nums ${gain ? 'text-profit' : 'text-loss'}`}>{gain ? '+' : ''}{Number(x.unrealised_pct ?? 0).toFixed(2)}%</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/* Agent watchlist — add/remove stocks the agent scans & may paper-trade. */
function AgentWatchlistPanel() {
  const [list, setList]     = useState([])
  const [query, setQuery]   = useState('')
  const [results, setResults] = useState([])
  const [busy, setBusy]     = useState(false)

  const loadList = () => apiFetch('/api/v1/india/user-watchlist')
    .then((d) => setList(Array.isArray(d) ? d : (d?.symbols ?? d?.watchlist ?? [])))
    .catch(() => {})
  useEffect(() => { loadList() }, [])

  useEffect(() => {
    if (query.trim().length < 2) { setResults([]); return }
    const t = setTimeout(() => {
      apiFetch(`/api/v1/portfolios/search/stocks?q=${encodeURIComponent(query.trim())}`)
        .then((r) => setResults(Array.isArray(r) ? r.slice(0, 6) : [])).catch(() => setResults([]))
    }, 300)
    return () => clearTimeout(t)
  }, [query])

  const add = async (sym) => {
    setBusy(true)
    try {
      await apiFetch(`/api/v1/india/user-watchlist/${sym}`, { method: 'POST' })
      toast.success(`${sym} added — agent will scan it`)
      setQuery(''); setResults([]); loadList()
    } catch { toast.error('Add failed') } finally { setBusy(false) }
  }
  const remove = async (sym) => {
    try {
      await apiFetch(`/api/v1/india/user-watchlist/${sym.replace('.NS', '')}`, { method: 'DELETE' })
      toast.success(`${sym} removed`); loadList()
    } catch { toast.error('Remove failed') }
  }

  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Plus size={15} className="text-accent" />
        <h3 className="text-slate-100 font-semibold text-sm">Agent Watchlist</h3>
        <span className="text-[10px] text-muted">stocks the agent scans &amp; may paper-trade</span>
      </div>

      <div className="relative">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search NSE/BSE stock to add (e.g. RELIANCE)…"
          className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-muted focus:outline-none focus:border-accent"
        />
        {results.length > 0 && (
          <div className="absolute z-10 mt-1 w-full glass-panel border border-border rounded-lg shadow-xl overflow-hidden">
            {results.map((r) => (
              <button key={r.symbol} disabled={busy} onClick={() => add(r.ticker || r.symbol.replace('.NS', ''))}
                className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-surface/60 text-sm">
                <span className="text-slate-200">{r.ticker || r.symbol} <span className="text-muted text-xs">{r.name}</span></span>
                <span className="text-[10px] text-accent">+ add</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {list.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {list.map((w, i) => {
            const sym = typeof w === 'string' ? w : (w.symbol ?? '')
            return (
              <span key={i} className="inline-flex items-center gap-1.5 bg-surface/60 border border-border rounded-full pl-2.5 pr-1.5 py-1 text-xs text-slate-200">
                {sym.replace('.NS', '')}
                <button onClick={() => remove(sym)} className="text-muted hover:text-loss text-sm leading-none">×</button>
              </span>
            )
          })}
        </div>
      ) : (
        <p className="text-xs text-muted">No custom stocks yet — search above to add. The agent already scans the top NSE universe by default.</p>
      )}
    </div>
  )
}

/* Real Zerodha account — actual broker wallet balance + mutual fund holdings. */
function RealZerodhaAccountPanel() {
  const [margins, setMargins] = useState(null)
  const [mf, setMf] = useState([])

  useEffect(() => {
    const load = () => {
      apiFetch('/api/v1/zerodha/margins').then(setMargins).catch(() => {})
      apiFetch('/api/v1/zerodha/mf/holdings').then((d) => setMf(d?.holdings ?? [])).catch(() => {})
    }
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [])

  const eq = margins?.equity
  const fmt = (n) => formatINR(n ?? 0)
  const mfValue = mf.reduce((s, h) => s + ((h.last_price ?? 0) * (h.quantity ?? 0)), 0)

  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Briefcase size={16} className="text-amber-400" />
        <h3 className="text-slate-100 font-semibold text-sm">Real Zerodha Account</h3>
        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 uppercase">Live Broker</span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
          <div className="text-muted text-[10px] uppercase tracking-wider">Wallet Balance</div>
          <div className="text-slate-100 font-bold tabular-nums">{eq ? fmt(eq.available_cash) : '—'}</div>
          <div className="text-[9px] text-muted">available cash</div>
        </div>
        <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
          <div className="text-muted text-[10px] uppercase tracking-wider">Used Margin</div>
          <div className="text-slate-100 font-bold tabular-nums">{eq ? fmt(eq.used_margin) : '—'}</div>
        </div>
        <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
          <div className="text-muted text-[10px] uppercase tracking-wider">Net Balance</div>
          <div className="text-slate-100 font-bold tabular-nums">{eq ? fmt(eq.net) : '—'}</div>
        </div>
        <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
          <div className="text-muted text-[10px] uppercase tracking-wider">Mutual Funds</div>
          <div className="text-slate-100 font-bold tabular-nums">{mf.length ? fmt(mfValue) : '₹0'}</div>
          <div className="text-[9px] text-muted">{mf.length} fund{mf.length === 1 ? '' : 's'}</div>
        </div>
      </div>

      {mf.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-muted uppercase tracking-wider border-b border-border">
              {['Fund', 'Units', 'NAV', 'Value', 'P&L'].map((h) => <th key={h} className="text-left px-2 py-1.5 font-semibold">{h}</th>)}
            </tr></thead>
            <tbody>
              {mf.map((h, i) => {
                const val = (h.last_price ?? 0) * (h.quantity ?? 0)
                const pnl = h.pnl ?? 0
                return (
                  <tr key={i} className="border-b border-border/40">
                    <td className="px-2 py-1.5 text-slate-200">{h.fund}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-300">{Number(h.quantity ?? 0).toFixed(3)}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-400">{fmt(h.last_price)}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-100">{fmt(val)}</td>
                    <td className={`px-2 py-1.5 tabular-nums ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>{pnl >= 0 ? '+' : ''}{fmt(pnl)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-muted">No real holdings or mutual funds yet — this account has ₹{eq ? Number(eq.available_cash).toLocaleString('en-IN') : '0'} cash. Trading runs in paper mode (no real orders).</p>
      )}
    </div>
  )
}

/* Real Upstox account — actual broker wallet balance + holdings. */
function RealUpstoxAccountPanel() {
  const [margins, setMargins] = useState(null)
  const [holdings, setHoldings] = useState([])
  const [status, setStatus] = useState(null)

  useEffect(() => {
    const load = () => {
      getUpstoxStatus().then(d => setStatus(d?.data ?? d)).catch(() => {})
      getUpstoxMargins().then(d => setMargins(d?.equity ?? d)).catch(() => {})
      getUpstoxHoldings().then(d => setHoldings(Array.isArray(d) ? d : [])).catch(() => {})
    }
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [])

  if (!status?.authenticated) return null;

  const eq = margins
  const fmt = (n) => formatINR(n ?? 0)
  const hValue = holdings.reduce((s, h) => s + ((h.last_price ?? 0) * (h.quantity ?? 0)), 0)

  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Briefcase size={16} className="text-purple-400" />
        <h3 className="text-slate-100 font-semibold text-sm">Real Upstox Account</h3>
        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-300 border border-purple-500/30 uppercase">Live Broker</span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
          <div className="text-muted text-[10px] uppercase tracking-wider">Wallet Balance</div>
          <div className="text-slate-100 font-bold tabular-nums">{eq ? fmt(eq.available_margin) : '—'}</div>
          <div className="text-[9px] text-muted">available margin</div>
        </div>
        <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
          <div className="text-muted text-[10px] uppercase tracking-wider">Used Margin</div>
          <div className="text-slate-100 font-bold tabular-nums">{eq ? fmt(eq.used_margin) : '—'}</div>
        </div>
        <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
          <div className="text-muted text-[10px] uppercase tracking-wider">Payin Amount</div>
          <div className="text-slate-100 font-bold tabular-nums">{eq ? fmt(eq.payin_amount) : '—'}</div>
        </div>
        <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2">
          <div className="text-muted text-[10px] uppercase tracking-wider">Holdings Value</div>
          <div className="text-slate-100 font-bold tabular-nums">{holdings.length ? fmt(hValue) : '₹0'}</div>
          <div className="text-[9px] text-muted">{holdings.length} holding{holdings.length === 1 ? '' : 's'}</div>
        </div>
      </div>

      {holdings.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-muted uppercase tracking-wider border-b border-border">
              {['Symbol', 'Qty', 'Avg Price', 'LTP', 'Value', 'P&L'].map((h) => <th key={h} className="text-left px-2 py-1.5 font-semibold">{h}</th>)}
            </tr></thead>
            <tbody>
              {holdings.map((h, i) => {
                const val = (h.last_price ?? 0) * (h.quantity ?? 0)
                const pnl = h.pnl ?? 0
                return (
                  <tr key={i} className="border-b border-border/40">
                    <td className="px-2 py-1.5 text-slate-200">{h.tradingsymbol || h.company_name}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-300">{Number(h.quantity ?? 0)}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-400">{fmt(h.average_price)}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-400">{fmt(h.last_price)}</td>
                    <td className="px-2 py-1.5 tabular-nums text-slate-100">{fmt(val)}</td>
                    <td className={`px-2 py-1.5 tabular-nums ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>{pnl >= 0 ? '+' : ''}{fmt(pnl)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-muted">No real holdings yet — this account has ₹{eq ? Number(eq.available_margin).toLocaleString('en-IN') : '0'} margin.</p>
      )}
    </div>
  )
}

/* Paper F&O positions — index options & futures the agent holds. */
function FnOPositionsPanel() {
  const [data, setData] = useState(null)
  useEffect(() => {
    const load = () => apiFetch('/api/v1/india/fno/positions').then(setData).catch(() => {})
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [])

  if (!data) return null
  const fmt = (n) => formatINR(n ?? 0)

  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Zap size={15} className="text-cyan" />
        <h3 className="text-slate-100 font-semibold text-sm">F&O Positions (Index)</h3>
        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/30 uppercase">Paper</span>
        <Link to="/fno" className="text-xs text-accent hover:text-accent/80 inline-flex items-center gap-1 ml-auto">
          Open F&O <ExternalLink size={11} />
        </Link>
      </div>

      {data.count > 0 ? (
        <>
          <div className="flex items-center gap-4 text-xs">
            <span className="text-muted">Margin: <span className="text-slate-300">{fmt(data.total_margin)}</span></span>
            <span className={data.total_pnl >= 0 ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
              {data.total_pnl >= 0 ? '+' : ''}{fmt(data.total_pnl)} P&amp;L
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-muted uppercase tracking-wider border-b border-border">
                {['Contract', 'Lots', 'Entry', 'Current', 'P&L', 'Expiry'].map((h) => <th key={h} className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">{h}</th>)}
              </tr></thead>
              <tbody>
                {data.positions.map((p, i) => {
                  const gain = (p.pnl ?? 0) >= 0
                  const label = p.instrument_type === 'FUTURE'
                    ? `${p.underlying} FUT`
                    : `${p.underlying} ${Number(p.strike ?? 0).toFixed(0)} ${p.option_type}`
                  return (
                    <tr key={i} className="border-b border-border/40">
                      <td className="px-2 py-1.5 text-slate-200 font-medium">{label}</td>
                      <td className="px-2 py-1.5 tabular-nums text-slate-300">{p.lots}</td>
                      <td className="px-2 py-1.5 tabular-nums text-slate-400">{Number(p.entry ?? 0).toFixed(2)}</td>
                      <td className="px-2 py-1.5 tabular-nums text-slate-100">{Number(p.current ?? 0).toFixed(2)}</td>
                      <td className={`px-2 py-1.5 tabular-nums font-semibold ${gain ? 'text-profit' : 'text-loss'}`}>{gain ? '+' : ''}{fmt(p.pnl)} <span className="text-[10px] opacity-70">({Number(p.pnl_pct ?? 0).toFixed(1)}%)</span></td>
                      <td className="px-2 py-1.5 text-muted">{p.expiry?.slice(5)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="text-xs text-muted">No open F&O positions. The agent opens index options (NIFTY/BANKNIFTY) when its signal triggers.</p>
      )}
    </div>
  )
}

function TaxQuickView({ portfolioId }) {
  const [status,  setStatus]  = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!portfolioId) return
    setLoading(true)
    apiFetch(`/api/v1/tax/current-fy-status/${portfolioId}`)
      .then(d => { setStatus(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [portfolioId])

  if (loading) return <LoadingSpinner message="Loading tax summary…" />

  return (
    <div className="space-y-4">
      {status && (
        <>
          <div className="flex items-center justify-between flex-wrap gap-2">
            <p className="text-slate-300 text-sm font-semibold">
              {status.financial_year} — Quick Tax Summary
            </p>
            <span className="text-muted text-xs">{status.days_left_in_fy}d left in FY</span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Realized STCG', value: status.realized_stcg,   color: 'text-amber-400', sub: '@ 20%' },
              { label: 'Realized LTCG', value: status.realized_ltcg,   color: 'text-blue-400',  sub: '@ 12.5%' },
              { label: 'Losses Booked', value: status.realized_losses,  color: 'text-profit',    sub: 'set-off' },
              { label: 'Est. Tax',      value: status.estimated_tax_so_far, color: 'text-red-400', sub: 'incl. cess' },
            ].map(c => (
              <div key={c.label} className="rounded-xl border border-border p-3 space-y-1" style={{ background: '#0a0f1c' }}>
                <p className="text-muted text-[10px] uppercase tracking-widest">{c.label}</p>
                <p className={`text-base font-bold tabular-nums ${c.color}`}>{formatINR(c.value, 0)}</p>
                <p className="text-muted/60 text-[10px]">{c.sub}</p>
              </div>
            ))}
          </div>

          {status.ltcg_exemption_remaining > 0 && (
            <div className="flex items-center gap-2 rounded-xl border border-profit/30 px-4 py-2.5 bg-profit/5">
              <Zap size={13} className="text-profit flex-shrink-0" />
              <p className="text-xs text-profit font-medium">
                {formatINR(status.ltcg_exemption_remaining, 0)} of ₹1.25L LTCG exemption still unused this year — consider harvesting gains
              </p>
            </div>
          )}
        </>
      )}

      <Link
        to={`/tax?portfolio=${portfolioId}`}
        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-accent/30 bg-accent/10 text-accent text-xs font-semibold hover:bg-accent/20 transition-colors"
      >
        <Receipt size={12} /> View Full Tax Calculator
        <ExternalLink size={11} className="text-accent/70" />
      </Link>
    </div>
  )
}

const TABS = [
  { id: 'holdings',     label: 'Holdings',     icon: List       },
  { id: 'allocation',   label: 'Allocation',   icon: PieIcon    },
  { id: 'tax',          label: 'Tax Summary',  icon: Receipt    },
  { id: 'transactions', label: 'Trade History', icon: BarChart2  },
  { id: 'doctor',       label: 'Doctor',        icon: Stethoscope },
]

export default function PortfolioTracker() {
  const {
    portfolios, activeId, setActiveId, detail, summary,
    loading, detailLoading, reload, refreshPortfolios,
    createPortfolio, deletePortfolio,
    addHolding, sellHolding, deleteHolding,
    searchStocks, getTransactions,
  } = usePortfolioTracker()

  const [tab,            setTab]            = useState('holdings')
  const [showAdd,        setShowAdd]        = useState(false)
  const [sellTarget,     setSellTarget]     = useState(null)
  const [txSymbol,       setTxSymbol]       = useState(null)
  const [txData,         setTxData]         = useState([])
  const [txLoading,      setTxLoading]      = useState(false)
  const [quickCreate,    setQuickCreate]    = useState(false)
  const [quickName,      setQuickName]      = useState('')
  const [quickCreating,  setQuickCreating]  = useState(false)
  const [zStatus,        setZStatus]        = useState(null)
  const [zBusy,          setZBusy]          = useState(false)
  const [uBusy,          setUBusy]          = useState(false)
  const [uStatus,        setUStatus]        = useState(null)

  // ── Zerodha connection: status, sync, and direct-login popup ──────────────
  async function fetchZStatus() {
    try { setZStatus(await getZerodhaStatus()) } catch { setZStatus(null) }
  }
  async function fetchUStatus() {
    try { setUStatus(await getUpstoxStatus()) } catch { setUStatus(null) }
  }

  useEffect(() => { fetchZStatus(); fetchUStatus(); }, [])

  // Pull live Zerodha holdings into a "Zerodha Demat" portfolio (auto-created
  // by the backend) and surface it on this page.
  async function syncZerodha() {
    setZBusy(true)
    try {
      const d = await apiFetch('/api/v1/portfolios/sync-zerodha', { method: 'POST' })
      toast.success(`Synced ${d.synced ?? 0} Zerodha holdings`)
      await refreshPortfolios()          // refresh the portfolios LIST (fixes "No portfolios yet")
      if (d.portfolio_id) setActiveId(d.portfolio_id)
      await reload()
    } catch (err) {
      const msg = (err?.message || '').includes('HTTP 4')
        ? 'Connect Zerodha first'
        : 'Zerodha sync failed'
      toast.error(msg)
    } finally {
      setZBusy(false)
    }
  }

  async function syncUpstox() {
    setUBusy(true)
    try {
      const d = await syncUpstoxHoldings()
      toast.success(`Synced ${d.synced ?? 0} Upstox holdings`)
      await refreshPortfolios()
      if (d.portfolio_id) setActiveId(d.portfolio_id)
      await reload()
    } catch (err) {
      toast.error('Upstox sync failed')
    } finally {
      setUBusy(false)
    }
  }

  // Open the Zerodha login popup DIRECTLY from this page (no page redirect).
  // The popup must open synchronously inside the click gesture or the browser's
  // popup blocker kills it; we then redirect the already-open popup to the URL.
  async function handleConnectZerodha() {
    if (zStatus?.connected) { await syncZerodha(); return }
    const popup = window.open('about:blank', 'zerodha_login', 'width=600,height=720,left=200,top=80')
    try {
      const res = await getZerodhaLoginUrl()
      if (popup && !popup.closed) {
        popup.location.href = res.url
        toast('Complete login in the popup window…')
      } else {
        window.location.href = res.url   // popup blocked → same-tab fallback
      }
    } catch (err) {
      if (popup && !popup.closed) popup.close()
      toast.error(err?.response?.data?.detail || 'Could not fetch login URL — check ZERODHA_API_KEY in .env')
    }
  }

  // The OAuth callback page postMessages 'zerodha_connected' to this window.
  // On success: refresh status, auto-sync holdings, and show them here.
  useEffect(() => {
    async function onMsg(e) {
      if (e.data === 'zerodha_connected') {
        await fetchZStatus()
        await syncZerodha()
      } else if (typeof e.data === 'string' && e.data.startsWith('zerodha_error')) {
        toast.error('Zerodha login failed — try again')
      } else if (e.data === 'upstox_connected') {
        await fetchUStatus()
      } else if (typeof e.data === 'string' && e.data.startsWith('upstox_error')) {
        toast.error('Upstox login failed — try again')
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadTransactions(symbol) {
    setTxLoading(true)
    try {
      const data = await getTransactions(symbol)
      setTxData(Array.isArray(data) ? data : [])
    } catch {
      setTxData([])
    } finally {
      setTxLoading(false)
    }
  }

  function handleTabChange(id) {
    setTab(id)
    if (id === 'transactions') loadTransactions(null)
  }

  async function handleDelete(holding) {
    if (!confirm(`Remove ${holding.symbol?.replace('.NS', '')} from portfolio? This cannot be undone.`)) return
    try {
      await deleteHolding(holding.id)
      toast.success('Holding removed')
    } catch {
      toast.error('Failed to remove')
    }
  }

  if (loading) return <LoadingSpinner message="Loading portfolios…" />

  const hasPortfolio = portfolios.length > 0
  const holdings     = detail?.holdings || []

  return (
    <div className="space-y-5 fade-in">

      {/* ── Header ── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <Briefcase size={18} className="text-cyan" />
            {activeId ? portfolios.find(p => p.id === activeId)?.name || 'My Portfolio' : 'My Portfolio'}
          </h1>
          <p className="text-muted text-sm mt-0.5">
            Stocks + mutual funds + broker-synced holdings · agent paper-trades alongside until you flip
            <code className="mx-1 px-1 py-0.5 rounded bg-surface/60 border border-border text-[10px]">PAPER_MODE=false</code>
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <PortfolioSelector
            portfolios={portfolios}
            activeId={activeId}
            onSelect={setActiveId}
            onCreate={createPortfolio}
            onDelete={deletePortfolio}
          />
          {/* Zerodha Connect / Login — always visible. Opens the Kite login
              popup DIRECTLY from this page; on success it auto-syncs holdings
              and shows them here (no redirect to a separate page). */}
          {zStatus?.connected ? (
            <span
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-emerald-500/30 bg-emerald-500/8 text-emerald-400 text-xs font-semibold"
              title={`Connected as ${zStatus.user_name || zStatus.user_id || ''} · token expires ${zStatus.token_expires_at || ''}`}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
              Zerodha · {zStatus.user_name?.split(' ')[0] || 'Connected'}
            </span>
          ) : (
            <button
              onClick={handleConnectZerodha}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-blue-500/30 bg-blue-500/8 text-blue-400 text-xs font-semibold hover:bg-blue-500/15 hover:border-blue-500/50 transition-colors"
              title="Open Zerodha Kite login to connect live prices + real holdings"
            >
              Connect / Login Zerodha <ExternalLink size={11} />
            </button>
          )}

          {/* Upstox Connect / Login */}
          {uStatus?.authenticated ? (
            <span
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-purple-500/30 bg-purple-500/8 text-purple-400 text-xs font-semibold"
              title={`Upstox Connected`}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-purple-400" />
              Upstox Connected
            </span>
          ) : (
            <button
              disabled={uBusy}
              onClick={async () => {
                setUBusy(true)
                try {
                  const res = await autoLoginUpstox()
                  if (res.success) {
                    toast.success('Upstox auto-login successful!')
                    await fetchUStatus()
                  }
                } catch (err) {
                  toast.error(err?.response?.data?.detail || 'Upstox Auto-login failed')
                } finally {
                  setUBusy(false)
                }
              }}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-purple-500/30 bg-purple-500/8 text-purple-400 text-xs font-semibold hover:bg-purple-500/15 hover:border-purple-500/50 transition-colors disabled:opacity-50"
              title="Auto-login Upstox to connect live prices + real holdings"
            >
              {uBusy ? <RefreshCw size={11} className="animate-spin" /> : <Zap size={11} />} Connect / Login Upstox
            </button>
          )}
          {/* Sync button — pull Zerodha holdings into a portfolio. Available as
              soon as you're connected, even before any local portfolio exists. */}
          {zStatus?.connected && (
            <button
              onClick={syncZerodha}
              disabled={zBusy}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-blue-500/30 bg-blue-500/8 text-blue-400 text-xs font-semibold hover:bg-blue-500/15 transition-colors disabled:opacity-50"
              title="Pull live Zerodha Demat holdings into this view"
            >
              <RefreshCw size={13} className={zBusy ? 'animate-spin' : ''} /> Sync Zerodha
            </button>
          )}

          {/* Sync button for Upstox */}
          {uStatus?.authenticated && (
            <button
              onClick={syncUpstox}
              disabled={uBusy}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-purple-500/30 bg-purple-500/8 text-purple-400 text-xs font-semibold hover:bg-purple-500/15 transition-colors disabled:opacity-50"
              title="Pull live Upstox Demat holdings into this view"
            >
              <RefreshCw size={13} className={uBusy ? 'animate-spin' : ''} /> Sync Upstox
            </button>
          )}
          {activeId && (
            <>
              <button
                onClick={reload}
                className="p-2 rounded-lg border border-border text-muted hover:text-white hover:border-accent/40 transition-colors"
                title="Refresh prices"
              >
                <RefreshCw size={14} className={detailLoading ? 'animate-spin' : ''} />
              </button>
              <button
                onClick={() => setShowAdd(true)}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold hover:opacity-90 transition-opacity"
              >
                <Plus size={14} /> Add Stock
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── No portfolio state ── */}
      {!hasPortfolio && (
        <div className="glass-panel border border-border rounded-xl p-12 text-center space-y-4">
          <Briefcase size={40} className="text-muted/40 mx-auto" />
          <p className="text-slate-300 font-semibold">No portfolios yet</p>
          {zStatus?.connected ? (
            <>
              <p className="text-muted text-sm">
                Connected to Zerodha as <span className="text-emerald-400 font-medium">{zStatus.user_name || zStatus.user_id}</span>.
                Pull your live Demat holdings, or create a portfolio manually.
              </p>
              <button
                onClick={syncZerodha}
                disabled={zBusy}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
              >
                <RefreshCw size={14} className={zBusy ? 'animate-spin' : ''} /> Sync Zerodha Holdings
              </button>
              <p className="text-muted/60 text-xs">or</p>
            </>
          ) : uStatus?.authenticated ? (
            <>
              <p className="text-muted text-sm">
                Connected to Upstox. Pull your live Demat holdings, or create a portfolio manually.
              </p>
              <button
                onClick={syncUpstox}
                disabled={uBusy}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-gradient-to-r from-purple-600 to-indigo-500 text-white text-sm font-semibold disabled:opacity-50"
              >
                <RefreshCw size={14} className={uBusy ? 'animate-spin' : ''} /> Sync Upstox Holdings
              </button>
              <p className="text-muted/60 text-xs">or</p>
            </>
          ) : (
            <p className="text-muted text-sm">
              Connect a broker (top right) to pull your real holdings, or create a portfolio manually.
            </p>
          )}
          {quickCreate ? (
            <form
              className="flex items-center gap-2 justify-center"
              onSubmit={async (e) => {
                e.preventDefault()
                if (!quickName.trim()) return
                setQuickCreating(true)
                try {
                  await createPortfolio(quickName.trim())
                  setQuickCreate(false)
                  setQuickName('')
                } catch { toast.error('Failed to create') }
                finally { setQuickCreating(false) }
              }}
            >
              <input
                autoFocus
                value={quickName}
                onChange={e => setQuickName(e.target.value)}
                placeholder="Portfolio name…"
                className="px-3 py-2 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
              />
              <button
                type="submit"
                disabled={quickCreating || !quickName.trim()}
                className="px-4 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
              >
                {quickCreating ? 'Creating…' : 'Create'}
              </button>
              <button type="button" onClick={() => setQuickCreate(false)} className="px-3 py-2 text-muted text-sm hover:text-white">
                Cancel
              </button>
            </form>
          ) : (
            <button
              onClick={() => setQuickCreate(true)}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold"
            >
              <Plus size={14} /> Create Portfolio
            </button>
          )}
        </div>
      )}

      {/* ── Active portfolio content ── */}
      {activeId && (
        <>
          {/* Summary cards */}
          {detailLoading && !detail ? (
            <LoadingSpinner message="Loading portfolio…" />
          ) : (
            <SummaryCards summary={summary} />
          )}

          {/* Full paper portfolio detail — all positions with live P&L */}
          <PaperPortfolioPanel />

          {/* F&O index positions (paper) */}
          <FnOPositionsPanel />

          {/* Real Zerodha account — wallet balance + mutual funds */}
          <RealZerodhaAccountPanel />

          {/* Real Upstox account — wallet balance + holdings */}
          <RealUpstoxAccountPanel />

          {/* Agent watchlist — add stocks the agent scans & may paper-trade */}
          <AgentWatchlistPanel />

          {/* Agent activity — recent decisions inline */}
          <AgentActivityPanel />

          {/* Tab bar */}
          <div className="flex items-center gap-0.5 glass-panel border border-border rounded-xl p-1 w-fit max-w-full overflow-x-auto scrollbar-none">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => handleTabChange(id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold shrink-0 whitespace-nowrap transition-colors ${
                  tab === id ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'
                }`}
              >
                <Icon size={12} />
                {label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="glass-panel border border-border rounded-xl p-4">
            {tab === 'holdings' && (
              <HoldingsTable
                holdings={holdings}
                onSell={h => setSellTarget(h)}
                onDelete={handleDelete}
              />
            )}

            {tab === 'allocation' && (
              detail?.allocation
                ? <AllocationCharts allocation={detail.allocation} />
                : <p className="text-muted text-sm text-center py-8">No holdings to chart.</p>
            )}

            {tab === 'tax' && (
              <TaxQuickView portfolioId={activeId} />
            )}

            {tab === 'transactions' && (
              txLoading ? (
                <LoadingSpinner message="Loading transactions…" />
              ) : (
                <TransactionsTab
                  transactions={txData}
                  onRefresh={() => loadTransactions(txSymbol)}
                />
              )
            )}

            {tab === 'doctor' && (
              <div className="rounded-xl border border-border p-5 space-y-4 glass-panel">
                <div className="flex items-center gap-2">
                  <Stethoscope size={16} className="text-red-400" />
                  <p className="text-slate-200 font-semibold text-sm">Portfolio Health Check</p>
                </div>
                <p className="text-muted text-xs">Get a full AI-powered diagnosis of concentration, tax efficiency, risk quality, and more.</p>
                <Link
                  to="/doctor"
                  className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl font-semibold text-sm text-white glass-panel"
                >
                  <Stethoscope size={14} />
                  Open Portfolio Doctor →
                </Link>
              </div>
            )}
          </div>
        </>
      )}

      {/* ── Modals ── */}
      {showAdd && (
        <AddHoldingModal
          onClose={() => setShowAdd(false)}
          onAdd={addHolding}
          searchStocks={searchStocks}
        />
      )}

      {sellTarget && (
        <SellModal
          holding={sellTarget}
          onClose={() => setSellTarget(null)}
          onSell={sellHolding}
        />
      )}
    </div>
  )
}
