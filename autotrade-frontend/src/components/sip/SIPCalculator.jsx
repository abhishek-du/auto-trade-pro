import { useState } from 'react'
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Legend,
} from 'recharts'
import { Calculator, Target, Clock } from 'lucide-react'

function fmtINR(n) {
  if (n == null) return '—'
  if (n >= 1e7)  return `₹${(n / 1e7).toFixed(2)}Cr`
  if (n >= 1e5)  return `₹${(n / 1e5).toFixed(2)}L`
  return `₹${(+n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function fmtL(v) {
  if (v >= 1e7)  return `${(v / 1e7).toFixed(1)}Cr`
  if (v >= 1e5)  return `${(v / 1e5).toFixed(1)}L`
  if (v >= 1000) return `${(v / 1000).toFixed(0)}K`
  return v?.toFixed(0)
}

const MODES = ['FV Calculator', 'Required SIP', 'Time to Target']

const DonutTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-panel border border-border rounded-lg px-3 py-2 text-xs">
      <p style={{ color: payload[0].payload.fill }}>{payload[0].name}</p>
      <p className="text-slate-200 font-semibold">{fmtINR(payload[0].value)}</p>
    </div>
  )
}

const ChartTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-panel border border-border rounded-lg px-3 py-2 text-xs space-y-1">
      <p className="text-muted">{label}</p>
      {payload.map(p => (
        <p key={p.dataKey} style={{ color: p.color }}>{p.name}: ₹{(p.value||0).toLocaleString('en-IN',{maximumFractionDigits:0})}</p>
      ))}
    </div>
  )
}

export default function SIPCalculator({ runCalculator, calcRequiredSIP, calcTimeToTarget }) {
  const [mode,    setMode]    = useState(0)
  const [loading, setLoading] = useState(false)
  const [result,  setResult]  = useState(null)
  const [err,     setErr]     = useState('')

  // FV mode fields
  const [fvForm, setFvForm] = useState({
    monthly_amount:    5000,
    years:             10,
    expected_return_pct: 12,
    current_corpus:    0,
    step_up_pct:       0,
  })

  // Required SIP fields
  const [reqForm, setReqForm] = useState({
    target_amount:      1000000,
    months:             120,
    expected_return_pct: 12,
  })

  // Time to target fields
  const [tttForm, setTttForm] = useState({
    monthly_sip:        5000,
    target_amount:      1000000,
    expected_return_pct: 12,
    current_corpus:      0,
  })

  const handleCalc = async () => {
    setLoading(true)
    setErr('')
    setResult(null)
    try {
      let data
      if (mode === 0)      data = await runCalculator(fvForm)
      else if (mode === 1) data = await calcRequiredSIP(reqForm)
      else                 data = await calcTimeToTarget(tttForm)
      setResult(data)
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }

  const chartData = result?.yearly_summary?.map(y => ({
    year:     `Y${y.year}`,
    invested: y.invested,
    corpus:   y.corpus,
  })) || []

  const donutData = result?.projected_value ? [
    { name: 'Invested', value: result.total_invested, fill: '#64748b' },
    { name: 'Gain',     value: result.absolute_gain,  fill: '#3b82f6' },
  ] : []

  return (
    <div className="space-y-5">
      {/* Mode tabs */}
      <div className="flex gap-1 bg-surface rounded-xl p-1 w-fit">
        {MODES.map((m, i) => (
          <button
            key={m}
            onClick={() => { setMode(i); setResult(null); setErr('') }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              mode === i ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'
            }`}
          >
            {i === 0 && <Calculator size={11} />}
            {i === 1 && <Target size={11} />}
            {i === 2 && <Clock size={11} />}
            {m}
          </button>
        ))}
      </div>

      {/* Inputs */}
      {mode === 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
          {[
            { label: 'Monthly SIP (₹)',    key: 'monthly_amount',    min: 100,  step: 500 },
            { label: 'Duration (years)',   key: 'years',             min: 1,    max: 40   },
            { label: 'Expected Return (%)',key: 'expected_return_pct',min: 1,   max: 50, step: 0.5 },
            { label: 'Current Corpus (₹)', key: 'current_corpus',    min: 0,   step: 1000 },
            { label: 'Annual Step-Up (%)', key: 'step_up_pct',       min: 0,   max: 30, step: 0.5 },
          ].map(f => (
            <div key={f.key} className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">{f.label}</label>
              <input
                type="number" min={f.min} max={f.max} step={f.step || 1}
                value={fvForm[f.key]}
                onChange={e => setFvForm(p => ({ ...p, [f.key]: +e.target.value }))}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
          ))}
        </div>
      )}

      {mode === 1 && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
          {[
            { label: 'Target Amount (₹)',  key: 'target_amount',      min: 1000,  step: 10000 },
            { label: 'Duration (months)',  key: 'months',             min: 1,     max: 480    },
            { label: 'Expected Return (%)',key: 'expected_return_pct',min: 1,     max: 50, step: 0.5 },
          ].map(f => (
            <div key={f.key} className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">{f.label}</label>
              <input
                type="number" min={f.min} max={f.max} step={f.step || 1}
                value={reqForm[f.key]}
                onChange={e => setReqForm(p => ({ ...p, [f.key]: +e.target.value }))}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
          ))}
        </div>
      )}

      {mode === 2 && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
          {[
            { label: 'Monthly SIP (₹)',    key: 'monthly_sip',        min: 100,   step: 500   },
            { label: 'Target Amount (₹)',  key: 'target_amount',      min: 1000,  step: 10000 },
            { label: 'Expected Return (%)',key: 'expected_return_pct',min: 1,     max: 50, step: 0.5 },
            { label: 'Current Corpus (₹)', key: 'current_corpus',     min: 0,     step: 1000  },
          ].map(f => (
            <div key={f.key} className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">{f.label}</label>
              <input
                type="number" min={f.min} max={f.max} step={f.step || 1}
                value={tttForm[f.key]}
                onChange={e => setTttForm(p => ({ ...p, [f.key]: +e.target.value }))}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={handleCalc} disabled={loading}
          className="px-6 py-2.5 bg-accent hover:bg-accent/90 disabled:opacity-50 text-white rounded-lg text-sm font-semibold transition-colors"
        >
          {loading ? 'Calculating…' : 'Calculate'}
        </button>
        {err && <p className="text-loss text-xs">{err}</p>}
      </div>

      {/* Results */}
      {result && mode === 0 && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-4 border-t border-border">
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Total Invested</p>
              <p className="text-slate-100 font-bold text-xl tabular-nums">{fmtINR(result.total_invested)}</p>
            </div>
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Projected Value</p>
              <p className="text-profit font-bold text-xl tabular-nums">{fmtINR(result.projected_value)}</p>
            </div>
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Absolute Gain</p>
              <p className="text-profit font-bold text-xl tabular-nums">{fmtINR(result.absolute_gain)}</p>
            </div>
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Return %</p>
              <p className="text-accent font-bold text-xl tabular-nums">+{result.absolute_gain_pct?.toFixed(1)}%</p>
            </div>
          </div>

          {result.final_corpus_with_existing > result.projected_value && (
            <p className="text-muted text-xs">
              With existing corpus of {fmtINR(result.current_corpus)} →
              <span className="text-profit font-semibold ml-1">
                Total: {fmtINR(result.final_corpus_with_existing)}
              </span>
            </p>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Donut */}
            {donutData.length > 0 && (
              <div>
                <p className="text-muted text-[10px] uppercase tracking-widest mb-3">Composition</p>
                <div className="h-48">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={donutData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3} dataKey="value">
                        {donutData.map((d, i) => <Cell key={i} fill={d.fill} />)}
                      </Pie>
                      <Tooltip content={<DonutTooltip />} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex justify-center gap-4 text-xs">
                  {donutData.map(d => (
                    <span key={d.name} className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full inline-block" style={{ background: d.fill }} />
                      <span className="text-muted">{d.name}: </span>
                      <span className="text-slate-200 font-semibold">{fmtINR(d.value)}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Area chart */}
            {chartData.length > 0 && (
              <div>
                <p className="text-muted text-[10px] uppercase tracking-widest mb-3">Year-wise Growth</p>
                <div className="h-48">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData}>
                      <defs>
                        <linearGradient id="gCorpus2" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="year" tick={{ fill: '#64748b', fontSize: 9 }} />
                      <YAxis tickFormatter={fmtL} tick={{ fill: '#64748b', fontSize: 9 }} width={44} />
                      <Tooltip content={<ChartTooltip />} />
                      <Area type="monotone" dataKey="invested" name="Invested" stroke="#64748b" strokeWidth={1.5} fill="none" />
                      <Area type="monotone" dataKey="corpus"   name="Value"    stroke="#3b82f6" strokeWidth={2}   fill="url(#gCorpus2)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {result && mode === 1 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 pt-4 border-t border-border">
          <div>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Required Monthly SIP</p>
            <p className="text-profit font-bold text-2xl tabular-nums">{fmtINR(result.required_monthly_sip)}</p>
          </div>
          <div>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Target Amount</p>
            <p className="text-slate-100 font-bold text-xl tabular-nums">{fmtINR(result.target_amount)}</p>
          </div>
          <div>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Duration</p>
            <p className="text-slate-100 font-bold text-xl">{result.months}m ({(result.months/12).toFixed(1)}y)</p>
          </div>
        </div>
      )}

      {result && mode === 2 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 pt-4 border-t border-border">
          <div>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Time to Target</p>
            <p className="text-profit font-bold text-2xl">
              {result.years > 0 ? `${result.years}y ` : ''}{result.remaining_months}m
            </p>
          </div>
          <div>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Total Months</p>
            <p className="text-slate-100 font-bold text-xl">{result.months}</p>
          </div>
          <div>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Target</p>
            <p className="text-slate-100 font-bold text-xl">{fmtINR(result.target_amount)}</p>
          </div>
        </div>
      )}
    </div>
  )
}
