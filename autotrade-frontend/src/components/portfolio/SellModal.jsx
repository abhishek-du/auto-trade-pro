import { useState, useEffect } from 'react'
import { X, TrendingDown } from 'lucide-react'
import toast from 'react-hot-toast'
import { formatINR, formatPct } from '../../utils/indianFormat'

export default function SellModal({ holding, onClose, onSell }) {
  const [qty,       setQty]       = useState('')
  const [price,     setPrice]     = useState(String(holding.current_price || holding.avg_buy_price || ''))
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [notes,     setNotes]     = useState('')
  const [submitting, setSubmitting] = useState(false)

  const sellQty    = parseFloat(qty)   || 0
  const sellPrice  = parseFloat(price) || 0
  const proceeds   = sellQty * sellPrice
  const costBasis  = sellQty * holding.avg_buy_price
  const pnl        = proceeds - costBasis
  const pnlPct     = costBasis > 0 ? pnl / costBasis * 100 : 0

  // STCG / LTCG classification based on first_buy_date
  const buyDate    = holding.first_buy_date ? new Date(holding.first_buy_date) : null
  const sellDt     = new Date(tradeDate)
  const holdDays   = buyDate ? Math.floor((sellDt - buyDate) / 86400000) : null
  const isLTCG     = holdDays != null && holdDays >= 365
  const taxRate    = isLTCG ? 0.125 : 0.20
  const taxLabel   = isLTCG ? 'LTCG 12.5%' : 'STCG 20%'
  const estimatedTax = pnl > 0 ? pnl * taxRate : 0

  async function handleSubmit(e) {
    e.preventDefault()
    if (!qty || !price) { toast.error('Enter quantity and price'); return }
    if (sellQty > holding.quantity) { toast.error(`Max ${holding.quantity} units`); return }
    setSubmitting(true)
    try {
      await onSell(holding.id, { quantity: sellQty, price: sellPrice, trade_date: tradeDate, notes })
      toast.success(`Sold ${sellQty} × ${holding.symbol?.replace('.NS', '')}`)
      onClose()
    } catch (err) {
      toast.error(err.message || 'Sell failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <div className="fixed right-0 top-0 h-full w-full max-w-sm z-50 flex flex-col bg-panel border-l border-border shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="text-slate-100 font-bold text-base flex items-center gap-2">
            <TrendingDown size={16} className="text-loss" /> Sell {holding.symbol?.replace('.NS', '')}
          </h2>
          <button onClick={onClose} className="p-1.5 rounded text-muted hover:text-white hover:bg-white/10">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Holding info */}
          <div className="p-3 rounded-lg bg-white/5 border border-border text-xs space-y-1">
            <div className="flex justify-between">
              <span className="text-muted">Holding</span>
              <span className="text-slate-200">{holding.quantity} shares</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Avg Cost</span>
              <span className="text-slate-200">{formatINR(holding.avg_buy_price)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">LTP</span>
              <span className="text-slate-200">{holding.current_price ? formatINR(holding.current_price) : '—'}</span>
            </div>
            {holdDays != null && (
              <div className="flex justify-between">
                <span className="text-muted">Holding period</span>
                <span className={isLTCG ? 'text-profit' : 'text-warn'}>{holdDays}d ({isLTCG ? 'LTCG' : 'STCG'})</span>
              </div>
            )}
          </div>

          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">
              Quantity to sell <span className="text-muted/60">(max {holding.quantity})</span>
            </label>
            <input
              type="number" min="0.001" step="any" max={holding.quantity}
              value={qty}
              onChange={e => setQty(e.target.value)}
              placeholder={`e.g. ${Math.min(holding.quantity, 5)}`}
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Sell Price (₹)</label>
            <input
              type="number" min="0.01" step="any"
              value={price}
              onChange={e => setPrice(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Sell Date</label>
            <input
              type="date"
              value={tradeDate}
              onChange={e => setTradeDate(e.target.value)}
              max={new Date().toISOString().slice(0, 10)}
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 outline-none focus:border-accent/50"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">Notes</label>
            <input
              value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder="Reason for selling…"
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
            />
          </div>

          {/* P&L preview */}
          {proceeds > 0 && (
            <div className="p-3 rounded-lg border space-y-1.5 text-xs"
              style={{ borderColor: pnl >= 0 ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)', background: pnl >= 0 ? 'rgba(34,197,94,0.05)' : 'rgba(239,68,68,0.05)' }}>
              <div className="flex justify-between">
                <span className="text-muted">Proceeds</span>
                <span className="text-slate-200 font-semibold">{formatINR(proceeds)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Realised P&L</span>
                <span className={`font-bold ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {formatINR(pnl)} ({formatPct(pnlPct)})
                </span>
              </div>
              {pnl > 0 && (
                <div className="flex justify-between">
                  <span className="text-muted">Est. tax ({taxLabel})</span>
                  <span className="text-warn">{formatINR(estimatedTax)}</span>
                </div>
              )}
            </div>
          )}
        </form>

        <div className="px-5 py-4 border-t border-border flex gap-3">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-border text-sm text-muted hover:text-white transition-colors">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !qty || !price}
            className="flex-1 py-2.5 rounded-lg bg-gradient-to-r from-red-600 to-rose-500 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {submitting ? 'Selling…' : 'Confirm Sell'}
          </button>
        </div>
      </div>
    </>
  )
}
