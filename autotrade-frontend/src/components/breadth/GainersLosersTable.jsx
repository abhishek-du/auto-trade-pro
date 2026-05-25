import { useState } from 'react'
import { formatINR, formatVolume, formatPct } from '../../utils/indianFormat'

const MEDALS = ['🥇', '🥈', '🥉']

function StockTable({ stocks, colorCls, emptyMsg }) {
  if (!stocks || stocks.length === 0) {
    return <div className="flex items-center justify-center h-40 text-muted text-xs">{emptyMsg}</div>
  }

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border">
          <th className="px-3 py-2 text-left text-muted text-[10px] font-semibold uppercase tracking-wider w-6">#</th>
          <th className="px-3 py-2 text-left text-muted text-[10px] font-semibold uppercase tracking-wider">Stock</th>
          <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">LTP</th>
          <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">Change%</th>
          <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">Volume</th>
        </tr>
      </thead>
      <tbody>
        {stocks.map((s, i) => {
          const pct = s.change_pct || 0
          const pos = pct >= 0
          return (
            <tr key={s.symbol || i}
              className={`border-b border-border/30 hover:bg-white/[0.03] transition-colors ${i === 0 ? 'bg-gradient-to-r from-transparent ' + (pos ? 'to-profit/5' : 'to-loss/5') : ''}`}>
              <td className="px-3 py-2 text-muted text-[10px]">
                {i < 3 ? MEDALS[i] : <span className="tabular-nums">{i + 1}</span>}
              </td>
              <td className="px-3 py-2">
                <div className="font-bold text-slate-200 text-[11px]">
                  {(s.symbol || '').replace('.NS', '')}
                </div>
                <div className="text-muted text-[9px] truncate max-w-[100px]">{s.name || s.symbol}</div>
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-slate-200 font-medium">
                {formatINR(s.ltp || s.price || 0)}
              </td>
              <td className="px-3 py-2 text-right">
                <span className={`text-[11px] font-bold tabular-nums px-1.5 py-0.5 rounded ${
                  pos ? 'bg-profit/10 text-profit' : 'bg-loss/10 text-loss'
                }`}>
                  {formatPct(pct)}
                </span>
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-muted">
                {formatVolume(s.volume || 0)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function ActiveTable({ stocks }) {
  if (!stocks || stocks.length === 0) {
    return <div className="flex items-center justify-center h-40 text-muted text-xs">No data</div>
  }

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border">
          <th className="px-3 py-2 text-left text-muted text-[10px] font-semibold uppercase tracking-wider w-6">#</th>
          <th className="px-3 py-2 text-left text-muted text-[10px] font-semibold uppercase tracking-wider">Stock</th>
          <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">LTP</th>
          <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">Change%</th>
          <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">Vol Ratio</th>
        </tr>
      </thead>
      <tbody>
        {stocks.map((s, i) => {
          const pct   = s.change_pct || 0
          const ratio = s.volume_ratio
          return (
            <tr key={s.symbol || i} className="border-b border-border/30 hover:bg-white/[0.03]">
              <td className="px-3 py-2 text-muted text-[10px] tabular-nums">{i + 1}</td>
              <td className="px-3 py-2">
                <div className="font-bold text-slate-200 text-[11px]">{(s.symbol || '').replace('.NS', '')}</div>
                <div className="text-muted text-[9px] truncate max-w-[100px]">{s.name || s.symbol}</div>
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-slate-200 font-medium">
                {formatINR(s.ltp || s.price || 0)}
              </td>
              <td className="px-3 py-2 text-right">
                <span className={`text-[11px] font-bold tabular-nums ${pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {formatPct(pct)}
                </span>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {ratio != null
                  ? <span className={`text-[11px] font-semibold ${ratio > 2 ? 'text-warn' : 'text-slate-300'}`}>
                      {ratio.toFixed(1)}x
                    </span>
                  : <span className="text-muted">—</span>
                }
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

const TABS = [
  { id: 'gainers',    label: 'Top Gainers',  colorCls: 'text-profit', activeBar: 'border-profit' },
  { id: 'losers',     label: 'Top Losers',   colorCls: 'text-loss',   activeBar: 'border-loss'   },
  { id: 'active',     label: 'Most Active',  colorCls: 'text-cyan',   activeBar: 'border-cyan'   },
]

export default function GainersLosersTable({ gainers = [], losers = [], mostActive = [], source }) {
  const [tab, setTab] = useState('gainers')

  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex border-b border-border">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={[
              'flex-1 py-2.5 text-xs font-semibold transition-colors',
              tab === t.id
                ? `${t.colorCls} border-b-2 ${t.activeBar}`
                : 'text-muted hover:text-slate-300',
            ].join(' ')}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {tab === 'gainers' && (
          <StockTable stocks={gainers} colorCls="text-profit" emptyMsg="No gainers data" />
        )}
        {tab === 'losers' && (
          <StockTable stocks={losers} colorCls="text-loss" emptyMsg="No losers data" />
        )}
        {tab === 'active' && (
          <ActiveTable stocks={mostActive} />
        )}
      </div>

      {/* Source badge */}
      <div className="px-3 py-2 border-t border-border">
        <span className={`text-[10px] font-semibold ${source === 'NSE_API' || source === 'MIXED' ? 'text-profit' : 'text-muted'}`}>
          {source === 'NSE_API' || source === 'MIXED' ? '● NSE Live Data' : '○ Watchlist Only'}
        </span>
      </div>
    </div>
  )
}
