import { useState, useEffect, useRef } from 'react'
import { Plus, Trash2, Calculator } from 'lucide-react'
import TaxSummaryCards from './TaxSummaryCards'
import { apiFetch } from '../../api/client'
import TaxWaterfall    from './TaxWaterfall'

const ASSET_TYPES = [
  { value: 'EQUITY',           label: 'Listed Equity (Stocks)' },
  { value: 'EQUITY_MF',        label: 'Equity Mutual Fund (≥65%)' },
  { value: 'DEBT_MF_POST2023', label: 'Debt MF (bought after Apr-2023)' },
  { value: 'DEBT_MF_PRE2023',  label: 'Debt MF (bought before Apr-2023)' },
]

const FY_OPTIONS = ['FY2024-25', 'FY2025-26', 'FY2026-27']

function fmtHolding(days) {
  if (!days || days < 0) return ''
  if (days < 30)  return `${days} days`
  if (days < 365) return `${Math.round(days/30)} months`
  return `${(days/365).toFixed(1)} years`
}

function ClassificationHint({ trade }) {
  const [hint, setHint] = useState(null)
  const timer = useRef(null)

  useEffect(() => {
    if (!trade.buy_date || !trade.sell_date || !trade.asset_type) { setHint(null); return }
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      try {
        const data = await apiFetch('/api/v1/tax/classify-trade', {
          method: 'POST',
          body:   JSON.stringify({
            symbol:       trade.symbol || 'STOCK',
            company_name: '',
            asset_type:   trade.asset_type,
            buy_date:     trade.buy_date,
            sell_date:    trade.sell_date,
            buy_price:    trade.buy_price  || 100,
            sell_price:   trade.sell_price || 100,
            quantity:     trade.quantity   || 1,
          }),
        })
        setHint(data)
      } catch { setHint(null) }
    }, 400)
    return () => clearTimeout(timer.current)
  }, [trade.buy_date, trade.sell_date, trade.asset_type])

  if (!hint) return null

  const color = hint.gain_type === 'LTCG' ? 'text-blue-400 bg-blue-500/10 border-blue-500/20' :
                hint.gain_type === 'STCG' && !hint.is_slab_taxed ? 'text-amber-400 bg-amber-500/10 border-amber-500/20' :
                'text-purple-400 bg-purple-500/10 border-purple-500/20'

  const label = hint.gain_type === 'LTCG'     ? `LTCG (${(hint.tax_rate*100).toFixed(1)}%) — held ${fmtHolding(hint.holding_days)} > 12 months` :
                hint.gain_type === 'DEBT_SLAB' ? `Debt (slab rate) — held ${fmtHolding(hint.holding_days)}` :
                hint.is_slab_taxed             ? `STCG at slab rate — held ${fmtHolding(hint.holding_days)}` :
                `STCG (${(hint.tax_rate*100).toFixed(1)}%) — held ${fmtHolding(hint.holding_days)} ≤ 12 months`

  return (
    <div className={`px-3 py-1.5 rounded-lg border text-[10px] font-semibold ${color}`}>
      {label}
    </div>
  )
}

const EMPTY_TRADE = {
  symbol: '', company_name: '', asset_type: 'EQUITY',
  buy_date: '', sell_date: '', buy_price: '', sell_price: '', quantity: '',
}

export default function StandaloneCalculator() {
  const [trades,    setTrades]    = useState([])
  const [form,      setForm]      = useState({ ...EMPTY_TRADE })
  const [fy,        setFy]        = useState('FY2025-26')
  const [income,    setIncome]    = useState(1000000)
  const [result,    setResult]    = useState(null)
  const [loading,   setLoading]   = useState(false)
  const [err,       setErr]       = useState('')

  const set = (k, v) => setForm(p => ({ ...p, [k]: v }))

  const addTrade = () => {
    if (!form.symbol || !form.buy_date || !form.sell_date || !form.buy_price || !form.sell_price || !form.quantity) {
      setErr('Fill all fields before adding trade')
      return
    }
    setErr('')
    setTrades(p => [...p, { ...form, id: Date.now() }])
    setForm({ ...EMPTY_TRADE })
    setResult(null)
  }

  const removeTrade = (id) => {
    setTrades(p => p.filter(t => t.id !== id))
    setResult(null)
  }

  const calculate = async () => {
    if (!trades.length) { setErr('Add at least one trade'); return }
    setLoading(true); setErr(''); setResult(null)
    try {
      const data = await apiFetch('/api/v1/tax/calculate', {
        method: 'POST',
        body:   JSON.stringify({
          financial_year: fy,
          annual_income:  income,
          already_used_ltcg: 0,
          trades: trades.map(t => ({
            symbol:       t.symbol,
            company_name: t.company_name || t.symbol,
            asset_type:   t.asset_type,
            buy_date:     t.buy_date,
            sell_date:    t.sell_date,
            buy_price:    +t.buy_price,
            sell_price:   +t.sell_price,
            quantity:     +t.quantity,
          })),
        }),
      })
      setResult(data)
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-5">
      {/* Settings row */}
      <div className="flex flex-wrap gap-4">
        <div className="space-y-1">
          <label className="text-muted text-[10px] uppercase tracking-widest">Financial Year</label>
          <select value={fy} onChange={e => setFy(e.target.value)}
            className="bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent">
            {FY_OPTIONS.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-muted text-[10px] uppercase tracking-widest">Annual Income (₹)</label>
          <input type="number" min="0" step="100000" value={income}
            onChange={e => setIncome(+e.target.value)}
            className="bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 w-36 focus:outline-none focus:border-accent" />
        </div>
      </div>

      {/* Add trade form */}
      <div className="rounded-xl border border-border p-4 space-y-4" style={{ background: '#0a0f1c' }}>
        <p className="text-slate-100 font-semibold text-sm flex items-center gap-1.5">
          <Plus size={13} /> Add Trade
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {[
            { label: 'Symbol',      key: 'symbol',       type: 'text',   placeholder: 'RELIANCE' },
            { label: 'Company',     key: 'company_name', type: 'text',   placeholder: 'optional' },
            { label: 'Buy Price ₹', key: 'buy_price',    type: 'number', min: '0.01' },
            { label: 'Sell Price ₹',key: 'sell_price',   type: 'number', min: '0.01' },
            { label: 'Quantity',    key: 'quantity',     type: 'number', min: '0.001' },
            { label: 'Buy Date',    key: 'buy_date',     type: 'date'  },
            { label: 'Sell Date',   key: 'sell_date',    type: 'date'  },
          ].map(f => (
            <div key={f.key} className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">{f.label}</label>
              <input
                type={f.type} min={f.min} placeholder={f.placeholder}
                value={form[f.key]}
                onChange={e => set(f.key, e.target.value)}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
          ))}
          <div className="space-y-1">
            <label className="text-muted text-[10px] uppercase tracking-widest">Asset Type</label>
            <select value={form.asset_type} onChange={e => set('asset_type', e.target.value)}
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent">
              {ASSET_TYPES.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </div>
        </div>

        {/* Live classification hint */}
        <ClassificationHint trade={form} />

        {err && <p className="text-loss text-xs">{err}</p>}

        <button onClick={addTrade}
          className="flex items-center gap-1.5 px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent rounded-lg text-xs font-semibold transition-colors">
          <Plus size={12} /> Add Trade
        </button>
      </div>

      {/* Trades list */}
      {trades.length > 0 && (
        <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0a0f1c' }}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted text-[9px] uppercase tracking-wider">
                  <th className="text-left px-4 py-2.5">Symbol</th>
                  <th className="text-left px-3 py-2.5">Type</th>
                  <th className="text-right px-3 py-2.5">Buy Date</th>
                  <th className="text-right px-3 py-2.5">Sell Date</th>
                  <th className="text-right px-3 py-2.5">Qty</th>
                  <th className="text-right px-3 py-2.5">Buy ₹</th>
                  <th className="text-right px-3 py-2.5">Sell ₹</th>
                  <th className="text-right px-3 py-2.5">Gain/Loss</th>
                  <th className="px-3 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {trades.map(t => {
                  const gain = (+t.sell_price - +t.buy_price) * +t.quantity
                  return (
                    <tr key={t.id} className="hover:bg-white/2">
                      <td className="px-4 py-2 font-semibold text-slate-200">{t.symbol}</td>
                      <td className="px-3 py-2 text-muted">{t.asset_type.replace('_', ' ')}</td>
                      <td className="px-3 py-2 text-right text-muted">{t.buy_date}</td>
                      <td className="px-3 py-2 text-right text-muted">{t.sell_date}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted">{t.quantity}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-300">₹{(+t.buy_price).toLocaleString('en-IN')}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-300">₹{(+t.sell_price).toLocaleString('en-IN')}</td>
                      <td className={`px-3 py-2 text-right tabular-nums font-semibold ${gain >= 0 ? 'text-profit' : 'text-loss'}`}>
                        ₹{gain.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                      </td>
                      <td className="px-3 py-2">
                        <button onClick={() => removeTrade(t.id)} className="text-muted hover:text-red-400 transition-colors">
                          <Trash2 size={11} />
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Calculate */}
      {trades.length > 0 && (
        <button onClick={calculate} disabled={loading}
          className="flex items-center gap-2 px-6 py-2.5 bg-accent hover:bg-accent/90 disabled:opacity-50 text-white rounded-lg text-sm font-semibold transition-colors">
          <Calculator size={14} />
          {loading ? 'Calculating…' : `Calculate Tax (${trades.length} trades)`}
        </button>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-5 pt-2 border-t border-border">
          <TaxSummaryCards taxSummary={result} />
          <TaxWaterfall    taxSummary={result} />
        </div>
      )}
    </div>
  )
}
