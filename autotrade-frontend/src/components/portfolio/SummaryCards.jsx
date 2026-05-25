import { TrendingUp, TrendingDown, IndianRupee, Activity, Zap } from 'lucide-react'
import { formatINR, formatPct } from '../../utils/indianFormat'

function Card({ title, value, sub, trend, icon: Icon, highlight }) {
  const pos = trend == null ? null : trend >= 0
  return (
    <div
      className="bg-panel border border-border rounded-xl p-4 flex flex-col gap-2"
      style={highlight ? { borderColor: 'rgba(6,182,212,0.3)', background: 'rgba(6,182,212,0.04)' } : {}}
    >
      <div className="flex items-center justify-between">
        <span className="text-muted text-xs font-semibold uppercase tracking-wider">{title}</span>
        {Icon && <Icon size={14} className="text-muted/60" />}
      </div>
      <p className={`text-xl font-bold tabular-nums leading-none ${
        pos === null ? 'text-slate-100' : pos ? 'text-profit' : 'text-loss'
      }`}>
        {value}
      </p>
      {sub && <p className="text-muted text-[11px]">{sub}</p>}
    </div>
  )
}

export default function SummaryCards({ summary }) {
  if (!summary) return null

  const {
    total_invested, current_value, total_pnl, total_pnl_pct,
    today_pnl, xirr, holdings_count,
  } = summary

  return (
    <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
      <Card
        title="Current Value"
        value={formatINR(current_value)}
        sub={`${holdings_count} stocks`}
        icon={IndianRupee}
        highlight
      />
      <Card
        title="Invested"
        value={formatINR(total_invested)}
        sub="Total cost basis"
        icon={IndianRupee}
      />
      <Card
        title="Total P&L"
        value={formatINR(total_pnl)}
        sub={formatPct(total_pnl_pct)}
        trend={total_pnl}
        icon={total_pnl >= 0 ? TrendingUp : TrendingDown}
      />
      <Card
        title="Today's P&L"
        value={formatINR(today_pnl)}
        trend={today_pnl}
        icon={Activity}
      />
      <Card
        title="XIRR"
        value={xirr != null ? formatPct(xirr) : '—'}
        sub="Annualised return"
        trend={xirr}
        icon={Zap}
      />
    </div>
  )
}
