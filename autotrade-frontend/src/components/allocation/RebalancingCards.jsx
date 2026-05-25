import { useState } from 'react'
import { TrendingUp, TrendingDown, Minus, ChevronDown, ChevronUp } from 'lucide-react'
import { ASSET_CLASSES } from '../../hooks/useAllocation'
import { formatINR } from '../../utils/indianFormat'

const PRIORITY_COLOR = { HIGH: '#EF4444', MEDIUM: '#F59E0B', LOW: '#6B7280' }
const ACTION_CFG = {
  BUY:  { label: 'BUY',  bg: 'rgba(34,197,94,0.1)',  border: 'rgba(34,197,94,0.3)',  text: '#22C55E',  Icon: TrendingUp },
  SELL: { label: 'SELL', bg: 'rgba(239,68,68,0.1)',   border: 'rgba(239,68,68,0.3)',  text: '#EF4444',  Icon: TrendingDown },
  HOLD: { label: 'HOLD', bg: 'rgba(100,116,139,0.06)',border: 'rgba(100,116,139,0.2)',text: '#6B7280', Icon: Minus },
}

function ActionCard({ item, threshold }) {
  const cfg  = ACTION_CFG[item.action] || ACTION_CFG.HOLD
  const Icon = cfg.Icon
  const ac   = ASSET_CLASSES[item.asset_class] || {}
  const dev  = item.deviation_pct

  const barRange = Math.max(threshold * 2.5, Math.abs(dev) * 1.4)
  const currentX = 50 + (dev / barRange) * 50

  return (
    <div
      className="rounded-xl border p-4 space-y-3"
      style={{ borderLeft: `3px solid ${cfg.text}`, borderColor: `${cfg.text}30`, background: '#0a0f1c', borderLeftColor: cfg.text }}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-lg">{ac.emoji || '•'}</span>
          <div>
            <p className="text-slate-200 text-sm font-semibold">{ac.label || item.asset_class}</p>
            <p className="text-muted text-[10px]">{item.current_pct.toFixed(1)}% current → {item.target_pct.toFixed(1)}% target</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded" style={{ background: `${PRIORITY_COLOR[item.priority]}20`, color: PRIORITY_COLOR[item.priority] }}>
            {item.priority}
          </span>
          <div className="flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-bold" style={{ background: cfg.bg, border: `1px solid ${cfg.border}`, color: cfg.text }}>
            <Icon size={10} />
            {cfg.label}
          </div>
        </div>
      </div>

      {/* Deviation bar */}
      <div className="space-y-1">
        <div className="relative h-2 bg-surface rounded-full overflow-hidden">
          <div className="absolute top-0 bottom-0 w-px bg-slate-500/60" style={{ left: '50%' }} />
          <div
            className="absolute top-0 bottom-0 rounded-full"
            style={{
              left:  dev >= 0 ? '50%' : `${currentX}%`,
              width: `${Math.abs(dev / barRange) * 50}%`,
              background: cfg.text,
              opacity: 0.75,
            }}
          />
        </div>
        <div className="flex justify-between text-[9px] text-muted/40">
          <span>underweight</span><span>on target</span><span>overweight</span>
        </div>
      </div>

      {/* Amount + suggestion */}
      {item.action !== 'HOLD' && (
        <p className="text-xs text-muted leading-relaxed">
          {item.suggestion}
          {item.amount_inr > 0 && (
            <span className="text-slate-200 font-semibold"> ({formatINR(item.amount_inr, 0)})</span>
          )}
        </p>
      )}
    </div>
  )
}

export default function RebalancingCards({ rebalancing, newInvestment, onNewInvestmentChange, threshold = 5 }) {
  const [showHold, setShowHold] = useState(false)

  if (!rebalancing?.length) return (
    <div className="flex items-center justify-center h-32 text-muted text-sm">No rebalancing data</div>
  )

  const buys  = rebalancing.filter(r => r.action === 'BUY')
  const sells = rebalancing.filter(r => r.action === 'SELL')
  const holds = rebalancing.filter(r => r.action === 'HOLD')
  const active = [...sells, ...buys].sort((a, b) => b.priority === 'HIGH' ? 1 : -1)

  const totalBuy  = buys.reduce((s, r) => s + r.amount_inr, 0)
  const totalSell = sells.reduce((s, r) => s + r.amount_inr, 0)

  return (
    <div className="space-y-4">
      {/* Summary strip */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 rounded-xl border border-border text-xs" style={{ background: '#0a0f1c' }}>
        <span className="text-loss font-semibold">{sells.length} overweight</span>
        <span className="text-muted">·</span>
        <span className="text-profit font-semibold">{buys.length} underweight</span>
        <span className="text-muted">·</span>
        <span className="text-muted">{holds.length} on track</span>
        {(totalBuy + totalSell) > 0 && (
          <>
            <span className="ml-auto text-muted">Rebalancing needed:</span>
            <span className="text-amber-400 font-semibold">{formatINR(Math.max(totalBuy, totalSell), 0)}</span>
          </>
        )}
      </div>

      {/* New investment input */}
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-muted text-xs">If I invest more:</label>
        <div className="flex items-center gap-1.5 rounded-lg border border-border bg-surface px-2 py-1">
          <span className="text-muted text-xs">₹</span>
          <input
            type="number" min="0" step="1000"
            value={newInvestment}
            onChange={e => onNewInvestmentChange?.(+e.target.value)}
            className="bg-transparent text-slate-200 text-xs w-24 focus:outline-none"
            placeholder="50,000"
          />
        </div>
        {newInvestment > 0 && (
          <p className="text-[10px] text-muted">Will be directed to underweight classes first</p>
        )}
      </div>

      {/* Action cards */}
      <div className="space-y-3">
        {active.map((r, i) => <ActionCard key={i} item={r} threshold={threshold} />)}
      </div>

      {/* Show/hide HOLD cards */}
      {holds.length > 0 && (
        <>
          <button
            onClick={() => setShowHold(!showHold)}
            className="flex items-center gap-1.5 text-muted text-xs hover:text-slate-300 transition-colors"
          >
            {showHold ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {showHold ? 'Hide' : 'Show'} {holds.length} on-target classes
          </button>
          {showHold && (
            <div className="space-y-3 opacity-60">
              {holds.map((r, i) => <ActionCard key={i} item={r} threshold={threshold} />)}
            </div>
          )}
        </>
      )}
    </div>
  )
}
