import { useState } from 'react'
import { Play, Loader } from 'lucide-react'
import { formatINR } from '../../utils/indianFormat'

export default function BacktestPanel({ runBacktest }) {
  const [symbol,    setSymbol]    = useState('RELIANCE.NS')
  const [timeframe, setTimeframe] = useState('1h')
  const [fundGrade, setFundGrade] = useState('WATCHLIST')
  const [macroBias, setMacroBias] = useState(0)
  const [loading,   setLoading]   = useState(false)
  const [result,    setResult]    = useState(null)
  const [error,     setError]     = useState(null)

  async function run() {
    setLoading(true)
    setError(null)
    try {
      const r = await runBacktest({ symbol, timeframe, fund_grade: fundGrade, macro_bias: macroBias, days_back: 365 })
      setResult(r)
    } catch (err) {
      setError(err.message || 'Backtest failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="rounded-xl border border-border" style={{ background: '#0F1829' }}>
      <div className="px-5 py-3 border-b border-border flex items-center justify-between">
        <h3 className="text-slate-200 font-semibold text-sm">Backtest</h3>
        <span className="text-muted text-[10px]">Varsity M7 cost model + 1.5:1 R:R gate</span>
      </div>
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())}
            placeholder="RELIANCE.NS"
            className="px-3 py-2 rounded-lg border border-border bg-bg text-xs text-slate-200 outline-none focus:border-accent/50" />
          <select value={timeframe} onChange={e => setTimeframe(e.target.value)}
            className="px-3 py-2 rounded-lg border border-border bg-bg text-xs text-slate-200 outline-none focus:border-accent/50">
            <option value="15m">15min</option>
            <option value="1h">1 hour</option>
            <option value="1d">1 day</option>
          </select>
          <select value={fundGrade} onChange={e => setFundGrade(e.target.value)}
            className="px-3 py-2 rounded-lg border border-border bg-bg text-xs text-slate-200 outline-none focus:border-accent/50">
            <option value="INVESTMENT">Investment</option>
            <option value="WATCHLIST">Watchlist</option>
            <option value="REJECT">Reject</option>
          </select>
          <select value={macroBias} onChange={e => setMacroBias(parseInt(e.target.value))}
            className="px-3 py-2 rounded-lg border border-border bg-bg text-xs text-slate-200 outline-none focus:border-accent/50">
            <option value={-2}>Macro: −2</option>
            <option value={-1}>Macro: −1</option>
            <option value={0}>Macro: 0</option>
            <option value={1}>Macro: +1</option>
            <option value={2}>Macro: +2</option>
          </select>
        </div>

        <button onClick={run} disabled={loading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50">
          {loading ? <><Loader size={14} className="animate-spin" /> Running…</> : <><Play size={14} /> Run Backtest</>}
        </button>

        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>
        )}

        {result && !result.error && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2">
            <Stat label="Total Return"   value={`${result.total_return_pct}%`} good={result.total_return_pct > 0} />
            <Stat label="Trades"         value={result.total_trades} />
            <Stat label="Win Rate"       value={`${result.win_rate_pct}%`} good={result.win_rate_pct >= 50} />
            <Stat label="Profit Factor"  value={result.profit_factor} good={result.profit_factor >= 1.3} />
            <Stat label="Avg Win"        value={formatINR(result.avg_win_inr)} good />
            <Stat label="Avg Loss"       value={formatINR(result.avg_loss_inr)} bad />
            <Stat label="Max Drawdown"   value={`${result.max_drawdown_pct}%`} bad />
            <Stat label="Sharpe Annual"  value={result.sharpe_annual} good={result.sharpe_annual >= 1.0} />
          </div>
        )}
        {result?.error && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{result.error}</div>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, good, bad }) {
  const color = good ? 'text-emerald-400' : bad ? 'text-red-400' : 'text-slate-100'
  return (
    <div className="rounded-lg border border-border bg-white/[0.02] px-3 py-2">
      <p className="text-muted text-[10px] uppercase tracking-widest">{label}</p>
      <p className={`font-bold text-sm tabular-nums ${color}`}>{value}</p>
    </div>
  )
}
