import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Bot, Power, AlertTriangle, Activity, Zap, X, TrendingUp, TrendingDown, Clock } from 'lucide-react'
import { useAgent }       from '../hooks/useAgent'
import DecisionCard       from '../components/agent/DecisionCard'
import BacktestPanel      from '../components/agent/BacktestPanel'
import { formatINR }      from '../utils/indianFormat'
import { apiFetch } from '../api/client'

function StatusCard({ label, value, sub, color = 'text-slate-100' }) {
  return (
    <div className="rounded-xl border border-border p-4 space-y-1" style={{ background: '#0F1829' }}>
      <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">{label}</p>
      <p className={`font-bold text-xl tabular-nums ${color}`}>{value}</p>
      {sub && <p className="text-muted text-xs">{sub}</p>}
    </div>
  )
}

function PositionsTable({ positions, closePosition }) {
  if (!positions?.length) {
    return (
      <div className="rounded-xl border border-border p-6 text-center" style={{ background: '#0F1829' }}>
        <p className="text-muted text-sm">No open positions</p>
      </div>
    )
  }
  return (
    <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
      <div className="px-5 py-3 border-b border-border">
        <h3 className="text-slate-200 font-semibold text-sm">Open Positions</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border">
              {['Symbol','Side','Qty','Entry','Current','Stop','Target','P&L','Strategy',''].map(h => (
                <th key={h} className="text-left px-3 py-2 text-muted font-semibold uppercase tracking-wide whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {positions.map(p => (
              <tr key={p.symbol} className="hover:bg-white/[0.02]">
                <td className="px-3 py-2 font-bold text-slate-100">{p.symbol.replace('.NS','')}</td>
                <td className="px-3 py-2">
                  <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${p.side==='BUY' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>{p.side}</span>
                </td>
                <td className="px-3 py-2 tabular-nums">{p.qty}</td>
                <td className="px-3 py-2 tabular-nums">{formatINR(p.entry)}</td>
                <td className="px-3 py-2 tabular-nums">{p.current_price > 0 ? formatINR(p.current_price) : '—'}</td>
                <td className="px-3 py-2 tabular-nums text-red-400">{formatINR(p.stop)}</td>
                <td className="px-3 py-2 tabular-nums text-cyan">{formatINR(p.target)}</td>
                <td className={`px-3 py-2 tabular-nums font-bold ${p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {p.unrealized_pnl >= 0 ? '+' : ''}{formatINR(p.unrealized_pnl)}
                </td>
                <td className="px-3 py-2 text-muted text-[10px]">{p.strategy}</td>
                <td className="px-3 py-2">
                  <button onClick={() => closePosition(p.symbol)}
                    className="p-1 rounded hover:bg-red-500/10 text-muted hover:text-red-400">
                    <X size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function RulebookPreview() {
  const [open, setOpen] = useState(false)
  const [rules, setRules] = useState([])

  async function load() {
    if (rules.length > 0) { setOpen(!open); return }
    try {
      const d = await apiFetch('/api/v1/agent/rulebook')
      setRules(d.modules || [])
    } catch {}
    setOpen(true)
  }

  return (
    <div className="rounded-xl border border-border" style={{ background: '#0F1829' }}>
      <button onClick={load} className="w-full flex items-center justify-between px-5 py-3 hover:bg-white/[0.02] transition-colors">
        <h3 className="text-slate-200 font-semibold text-sm">Varsity Rulebook ({rules.length || 13} rules)</h3>
        <span className="text-cyan text-xs">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (
        <div className="border-t border-border max-h-72 overflow-y-auto">
          {rules.map(r => (
            <div key={r.id} className="px-4 py-2.5 border-b border-border/40">
              <div className="flex items-center gap-2">
                <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-cyan/15 text-cyan border border-cyan/30">{r.id}</span>
                <span className="text-muted text-[10px] uppercase">{r.module}</span>
              </div>
              <p className="text-slate-300 text-xs mt-1">{r.rule}</p>
              <p className="text-muted text-[10px] mt-0.5 font-mono">if: {r.condition} → {r.action}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function TradesHistory({ trades }) {
  const [tab, setTab] = useState('open')
  const open   = (trades || []).filter(t => !t.exit_ts)
  const closed = (trades || []).filter(t =>  t.exit_ts)
  const rows   = tab === 'open' ? open : closed

  const fmt = (n) => n == null ? '—' : Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  return (
    <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
      <div className="px-5 py-3 border-b border-border flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-slate-200 font-semibold text-sm flex items-center gap-2">
          <Clock size={14} className="text-cyan" /> Trade Book
        </h3>
        <div className="flex gap-1">
          {[['open', `Open (${open.length})`], ['closed', `Closed (${closed.length})`]].map(([key, label]) => (
            <button key={key} onClick={() => setTab(key)}
              className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${
                tab === key
                  ? 'bg-cyan/15 text-cyan border border-cyan/30'
                  : 'text-muted hover:text-slate-300 border border-transparent'
              }`}>{label}</button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="px-5 py-8 text-center text-muted text-sm">
          {tab === 'open' ? 'No open trades. Trigger a cycle to start.' : 'No closed trades yet.'}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border/60 bg-surface/30">
                {tab === 'open'
                  ? ['Symbol','Side','Qty','Entry ₹','Stop ₹','Target ₹','Strategy','Product','Since',''].map(h => (
                      <th key={h} className="text-left px-3 py-2 text-muted font-semibold uppercase tracking-wide whitespace-nowrap">{h}</th>
                    ))
                  : ['Symbol','Side','Qty','Entry ₹','Exit ₹','P&L ₹','Exit Reason','Strategy','Date',''].map(h => (
                      <th key={h} className="text-left px-3 py-2 text-muted font-semibold uppercase tracking-wide whitespace-nowrap">{h}</th>
                    ))
                }
              </tr>
            </thead>
            <tbody className="divide-y divide-border/30">
              {rows.map(t => {
                const isBuy = t.side === 'BUY'
                const pnlPos = (t.pnl || 0) >= 0
                return (
                  <tr key={t.id} className="hover:bg-white/[0.02]">
                    <td className="px-3 py-2">
                      <Link to={`/s/${t.symbol}`} className="font-bold text-slate-100 hover:text-cyan transition-colors">
                        {t.symbol.replace('.NS','')}
                      </Link>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${isBuy ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
                        {isBuy ? <span className="flex items-center gap-0.5"><TrendingUp size={9}/>{t.side}</span> : <span className="flex items-center gap-0.5"><TrendingDown size={9}/>{t.side}</span>}
                      </span>
                    </td>
                    <td className="px-3 py-2 tabular-nums text-slate-300">{t.qty}</td>
                    <td className="px-3 py-2 tabular-nums text-slate-300">₹{fmt(t.entry_price)}</td>
                    {tab === 'open' ? (
                      <>
                        <td className="px-3 py-2 tabular-nums text-red-400">₹{fmt(t.stop_price)}</td>
                        <td className="px-3 py-2 tabular-nums text-cyan">₹{fmt(t.target_price)}</td>
                      </>
                    ) : (
                      <>
                        <td className="px-3 py-2 tabular-nums text-slate-300">₹{fmt(t.exit_price)}</td>
                        <td className={`px-3 py-2 tabular-nums font-bold ${pnlPos ? 'text-emerald-400' : 'text-red-400'}`}>
                          {pnlPos ? '+' : ''}₹{fmt(t.pnl)}
                        </td>
                      </>
                    )}
                    <td className="px-3 py-2 text-muted text-[10px] font-mono">{t.strategy?.replace('_',' ')}</td>
                    {tab === 'open' ? (
                      <td className="px-3 py-2 text-muted text-[10px]">{t.product || 'CNC'}</td>
                    ) : (
                      <td className="px-3 py-2">
                        <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${
                          t.exit_reason === 'T2_TARGET' ? 'bg-emerald-500/15 text-emerald-400'
                          : t.exit_reason === 'T1_PARTIAL' ? 'bg-cyan/15 text-cyan'
                          : t.exit_reason === 'SL_HIT' ? 'bg-red-500/15 text-red-400'
                          : 'bg-amber-500/15 text-amber-400'
                        }`}>{t.exit_reason?.replace('_',' ') || '—'}</span>
                      </td>
                    )}
                    <td className="px-3 py-2 text-muted text-[10px] whitespace-nowrap">
                      {tab === 'open'
                        ? t.entry_ts?.slice(0,10)
                        : t.exit_ts?.slice(0,10)}
                    </td>
                    <td className="px-3 py-2">
                      {tab === 'open' && (
                        <Link to={`/s/${t.symbol}`} className="text-[10px] text-cyan hover:underline">View</Link>
                      )}
                    </td>
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

export default function TradingAgent() {
  const {
    status, decisions, trades, positions, performance,
    cycling, error,
    triggerCycle, closePosition, runBacktest, updateConfig,
  } = useAgent()

  const enabled    = status?.enabled
  const paperMode  = status?.paper_mode ?? true
  const portfolio  = status?.portfolio || {}

  const dailyPnl   = portfolio.daily_pnl_pct || 0
  const dailyColor = dailyPnl > 0 ? 'text-emerald-400' : dailyPnl < 0 ? 'text-red-400' : 'text-slate-100'

  async function toggleEnabled() {
    await updateConfig({ enabled: !enabled })
  }

  async function toggleMode() {
    if (paperMode === false) {
      // Going from live to paper — safe
      await updateConfig({ paper_mode: true })
      return
    }
    if (!confirm('Enabling LIVE mode will place REAL orders with REAL money. Continue?')) return
    if (!confirm('Are you absolutely sure? This is irreversible per session.')) return
    await updateConfig({ paper_mode: false })
  }

  return (
    <div className="space-y-5 fade-in">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl" style={{ background: 'rgba(139,92,246,0.12)' }}>
            <Bot size={20} className="text-violet-400" />
          </div>
          <div>
            <h1 className="text-slate-100 font-bold text-xl flex items-center gap-2">
              AI Trading Agent
              {enabled && (
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                  <span className="text-emerald-400 text-[10px] font-bold uppercase tracking-widest">Live</span>
                </span>
              )}
            </h1>
            <p className="text-muted text-sm">Varsity-grounded autonomous agent</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full border ${
            paperMode ? 'bg-blue-500/15 text-blue-400 border-blue-500/30' : 'bg-red-500/15 text-red-400 border-red-500/30 animate-pulse'
          }`}>{paperMode ? 'Paper Mode' : 'LIVE TRADING'}</span>
          <button onClick={toggleEnabled}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold transition-all ${
              enabled
                ? 'bg-red-500/15 text-red-400 border border-red-500/30 hover:bg-red-500/25'
                : 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25'
            }`}>
            <Power size={14} /> {enabled ? 'Disable' : 'Enable'}
          </button>
          <button onClick={triggerCycle} disabled={cycling}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50">
            <Zap size={14} className={cycling ? 'animate-pulse' : ''} /> {cycling ? 'Running…' : 'Manual Cycle'}
          </button>
        </div>
      </div>

      {/* Paper banner */}
      {paperMode && (
        <div className="rounded-lg border border-blue-500/30 bg-blue-500/8 px-4 py-2.5 flex items-center gap-2">
          <AlertTriangle size={14} className="text-blue-400 shrink-0" />
          <p className="text-blue-300 text-xs">
            <span className="font-semibold">Paper Trading Mode</span> — all decisions are simulated. No real orders will be placed. Switch to live only after 30+ days of paper validation.
          </p>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-2.5 flex items-center gap-2">
          <AlertTriangle size={14} className="text-red-400" />
          <p className="text-red-300 text-xs">{error}</p>
        </div>
      )}

      {/* Status cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatusCard label="Equity"
          value={formatINR(portfolio.equity || 0)}
          sub={`Free cash: ${formatINR(portfolio.cash || 0)}`} />
        <StatusCard label="Unrealised P&L"
          value={`${(portfolio.unrealised_pnl ?? 0) >= 0 ? '+' : ''}${formatINR(portfolio.unrealised_pnl ?? 0)}`}
          sub={`Realised: ${formatINR(portfolio.realised_pnl ?? 0)}`}
          color={(portfolio.unrealised_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
        <StatusCard label="Open Positions"  value={portfolio.open_positions_count || 0} sub={`Open risk: ${portfolio.open_risk_pct || 0}%`} />
        <StatusCard label="Decisions Today" value={status?.decisions_today || 0} sub={status?.session_active ? 'Session active' : 'After hours'} />
      </div>

      {/* Positions */}
      <PositionsTable positions={positions} closePosition={closePosition} />

      {/* Trade Book — open + closed trades */}
      <TradesHistory trades={trades} />

      {/* Decision feed + Backtest */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl border border-border" style={{ background: '#0F1829' }}>
          <div className="px-5 py-3 border-b border-border flex items-center justify-between">
            <h3 className="text-slate-200 font-semibold text-sm flex items-center gap-2">
              <Activity size={14} /> Decision Feed
            </h3>
            <span className="text-muted text-[10px]">Last {decisions?.length || 0}</span>
          </div>
          <div className="p-3 space-y-2 max-h-[500px] overflow-y-auto">
            {decisions?.length > 0 ? (
              decisions.map(d => <DecisionCard key={d.id} decision={d} />)
            ) : (
              <p className="text-muted text-xs text-center py-8">No decisions yet. Trigger a manual cycle or wait for the next scheduled run.</p>
            )}
          </div>
        </div>
        <div className="space-y-4">
          <BacktestPanel runBacktest={runBacktest} />
          <RulebookPreview />
        </div>
      </div>

      {/* Performance summary */}
      {performance && performance.total_trades > 0 && (
        <div className="rounded-xl border border-border" style={{ background: '#0F1829' }}>
          <div className="px-5 py-3 border-b border-border">
            <h3 className="text-slate-200 font-semibold text-sm">Performance Summary</h3>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 p-4">
            <StatusCard label="Trades"        value={performance.total_trades} />
            <StatusCard label="Win Rate"      value={`${performance.win_rate_pct}%`} color={performance.win_rate_pct >= 50 ? 'text-emerald-400' : 'text-amber-400'} />
            <StatusCard label="Profit Factor" value={performance.profit_factor} color={performance.profit_factor >= 1.3 ? 'text-emerald-400' : 'text-amber-400'} />
            <StatusCard label="Total P&L"     value={formatINR(performance.total_pnl)} color={performance.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'} />
          </div>
        </div>
      )}
    </div>
  )
}
