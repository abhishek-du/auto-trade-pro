import { useState } from 'react'
import { formatINR, formatPct } from '../../utils/indianFormat'

function StockList({ stocks, isHigh }) {
  if (!stocks || stocks.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-muted text-xs">
        No stocks {isHigh ? 'near 52W high' : 'near 52W low'} today
      </div>
    )
  }

  return (
    <div className="divide-y divide-border/40">
      {stocks.map(s => (
        <div key={s.symbol} className="flex items-center justify-between px-3 py-2 hover:bg-white/[0.03]">
          <div>
            <div className="text-slate-200 text-xs font-bold">{(s.symbol || '').replace('.NS', '')}</div>
            <div className="text-muted text-[10px] truncate max-w-[120px]">{s.name || s.symbol}</div>
          </div>
          <div className="text-right">
            <div className="text-slate-100 text-xs font-semibold tabular-nums">
              {formatINR(s.ltp || s.price || 0)}
            </div>
            <div className={`text-[10px] font-semibold tabular-nums ${(s.change_pct || 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
              {formatPct(s.change_pct || 0)}
            </div>
          </div>
          <div className="text-right min-w-[80px]">
            {isHigh
              ? s.from_52w_high != null && (
                  <span className="text-[9px] text-profit font-semibold">★ {s.from_52w_high?.toFixed(1) || '0.0'}% below H</span>
                )
              : s.from_52w_low != null && (
                  <span className="text-[9px] text-loss font-semibold">↓ {s.from_52w_low?.toFixed(1) || '0.0'}% above L</span>
                )
            }
          </div>
        </div>
      ))}
    </div>
  )
}

export default function Week52Panel({ week52High = [], week52Low = [] }) {
  const [tab, setTab] = useState('high')
  const highCount = week52High?.length || 0
  const lowCount  = week52Low?.length  || 0

  return (
    <div className="glass-panel border border-border rounded-xl overflow-hidden flex flex-col h-full">
      {/* Tabs */}
      <div className="flex border-b border-border">
        <button
          onClick={() => setTab('high')}
          className={[
            'flex-1 py-2.5 text-xs font-semibold transition-colors',
            tab === 'high'
              ? 'text-profit border-b-2 border-profit bg-profit/5'
              : 'text-muted hover:text-slate-300',
          ].join(' ')}
        >
          52W Highs
          <span className={`ml-1.5 text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
            tab === 'high' ? 'bg-profit/20 text-profit' : 'bg-muted/20 text-muted'
          }`}>{highCount}</span>
        </button>
        <button
          onClick={() => setTab('low')}
          className={[
            'flex-1 py-2.5 text-xs font-semibold transition-colors',
            tab === 'low'
              ? 'text-loss border-b-2 border-loss bg-loss/5'
              : 'text-muted hover:text-slate-300',
          ].join(' ')}
        >
          52W Lows
          <span className={`ml-1.5 text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
            tab === 'low' ? 'bg-loss/20 text-loss' : 'bg-muted/20 text-muted'
          }`}>{lowCount}</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <StockList stocks={tab === 'high' ? week52High : week52Low} isHigh={tab === 'high'} />
      </div>
    </div>
  )
}
