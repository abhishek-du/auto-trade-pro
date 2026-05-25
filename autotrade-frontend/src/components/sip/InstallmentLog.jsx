import { useState, useEffect } from 'react'
import { Plus, TrendingUp, TrendingDown } from 'lucide-react'

function fmtINR(n) {
  if (n == null) return '—'
  return `₹${(+n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function fmtPct(n) {
  if (n == null) return '—'
  return `${+n >= 0 ? '+' : ''}${(+n).toFixed(1)}%`
}

export default function InstallmentLog({ goalId, funds, getInstallments, addInstallment }) {
  const [rows,    setRows]    = useState([])
  const [loading, setLoading] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [saving,  setSaving]  = useState(false)
  const [err,     setErr]     = useState('')
  const [form, setForm] = useState({
    fund_id: '',
    scheme_code: '',
    scheme_name: '',
    amount: '',
    investment_date: new Date().toISOString().slice(0, 10),
  })

  const load = async () => {
    if (!goalId) return
    setLoading(true)
    try {
      const data = await getInstallments(goalId)
      setRows(Array.isArray(data) ? data : [])
    } catch { setRows([]) }
    finally  { setLoading(false) }
  }

  useEffect(() => { load() }, [goalId])

  const handleFundChange = (fundId) => {
    const f = funds.find(f => f.id === fundId)
    setForm(p => ({
      ...p,
      fund_id:     fundId,
      scheme_code: f?.scheme_code || '',
      scheme_name: f?.scheme_name || '',
    }))
  }

  const handleAdd = async () => {
    setErr('')
    if (!form.scheme_code || !form.amount || +form.amount <= 0) {
      setErr('Select a fund and enter a valid amount')
      return
    }
    setSaving(true)
    try {
      await addInstallment(goalId, {
        fund_id:         form.fund_id || null,
        scheme_code:     form.scheme_code,
        scheme_name:     form.scheme_name,
        amount:          +form.amount,
        investment_date: form.investment_date,
      })
      setShowAdd(false)
      setForm(p => ({ ...p, amount: '', fund_id: '', scheme_code: '', scheme_name: '' }))
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  const totalInvested = rows.reduce((s, r) => s + r.amount, 0)
  const totalValue    = rows.reduce((s, r) => s + (r.current_value || r.amount), 0)
  const totalGain     = totalValue - totalInvested

  return (
    <div className="space-y-4">
      {/* Summary strip */}
      {rows.length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Total Invested', value: fmtINR(totalInvested) },
            { label: 'Current Value',  value: fmtINR(totalValue), color: 'text-profit' },
            { label: 'Total Gain',     value: fmtINR(totalGain),  color: totalGain >= 0 ? 'text-profit' : 'text-loss' },
          ].map(m => (
            <div key={m.label} className="rounded-lg border border-border p-3" style={{ background: '#0a0f1c' }}>
              <p className="text-muted text-[9px] uppercase tracking-widest">{m.label}</p>
              <p className={`font-bold text-sm tabular-nums mt-0.5 ${m.color || 'text-slate-100'}`}>{m.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Add installment */}
      <div className="flex items-center justify-between">
        <p className="text-muted text-xs">{rows.length} installments</p>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-accent/20 hover:bg-accent/30 text-accent rounded-lg text-xs font-semibold transition-colors"
        >
          <Plus size={12} /> Add Installment
        </button>
      </div>

      {showAdd && (
        <div className="rounded-xl border border-border p-4 space-y-3" style={{ background: '#0a0f1c' }}>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">Fund</label>
              <select
                value={form.fund_id}
                onChange={e => handleFundChange(e.target.value)}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              >
                <option value="">Select fund…</option>
                {(funds || []).map(f => (
                  <option key={f.id} value={f.id}>{f.scheme_name}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">Amount (₹)</label>
              <input
                type="number" min="1" value={form.amount}
                onChange={e => setForm(p => ({ ...p, amount: e.target.value }))}
                placeholder="e.g. 5000"
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
            <div className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">Investment Date</label>
              <input
                type="date" value={form.investment_date}
                onChange={e => setForm(p => ({ ...p, investment_date: e.target.value }))}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
          </div>
          {err && <p className="text-loss text-xs">{err}</p>}
          <div className="flex gap-2">
            <button
              onClick={handleAdd} disabled={saving}
              className="px-4 py-2 bg-accent hover:bg-accent/90 disabled:opacity-50 text-white rounded-lg text-xs font-semibold transition-colors"
            >
              {saving ? 'Recording…' : 'Record Installment'}
            </button>
            <button onClick={() => setShowAdd(false)}
              className="px-4 py-2 bg-surface hover:bg-white/5 text-muted rounded-lg text-xs transition-colors">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <p className="text-muted text-sm text-center py-6">Loading…</p>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-32 gap-1">
          <p className="text-muted text-sm">No installments recorded yet</p>
          <p className="text-muted/50 text-xs">Add your first SIP installment above</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted text-[10px] uppercase tracking-wider">
                <th className="text-left px-3 py-2.5">Date</th>
                <th className="text-left px-3 py-2.5">Fund</th>
                <th className="text-right px-3 py-2.5">Amount</th>
                <th className="text-right px-3 py-2.5">NAV</th>
                <th className="text-right px-3 py-2.5">Units</th>
                <th className="text-right px-3 py-2.5">Cur Value</th>
                <th className="text-right px-3 py-2.5">Gain</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(r => (
                <tr key={r.id} className="hover:bg-white/2 transition-colors">
                  <td className="px-3 py-2.5 text-muted text-xs tabular-nums">{r.investment_date}</td>
                  <td className="px-3 py-2.5 text-slate-300 text-xs max-w-[160px] truncate">{r.scheme_name || r.scheme_code}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-200">{fmtINR(r.amount)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-muted text-xs">
                    {r.nav_at_purchase ? `₹${r.nav_at_purchase.toFixed(2)}` : '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-muted text-xs">{r.units_purchased}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-profit">{fmtINR(r.current_value)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">
                    <span className={r.gain >= 0 ? 'text-profit' : 'text-loss'}>
                      {fmtINR(r.gain)} <span className="text-[10px]">({fmtPct(r.gain_pct)})</span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
