import { useState, useEffect } from 'react'
import {
  Target, Plus, Calculator, X, ChevronRight,
  Shield, GraduationCap, Home, Car, Plane, Heart, TrendingUp,
  BarChart2, ListOrdered, Briefcase,
} from 'lucide-react'
import { apiFetch } from '../api/client'
import { useSIPTracker } from '../hooks/useSIPTracker'
import GoalCard           from '../components/sip/GoalCard'
import GoalProgressPanel  from '../components/sip/GoalProgressPanel'
import GoalGrowthChart    from '../components/sip/GoalGrowthChart'
import InstallmentLog     from '../components/sip/InstallmentLog'
import FundManager        from '../components/sip/FundManager'
import SIPCalculator      from '../components/sip/SIPCalculator'
import LoadingSpinner     from '../components/LoadingSpinner'

const GOAL_TYPES = [
  { id: 'retirement', label: 'Retirement',   icon: Shield,        color: 'text-purple-400' },
  { id: 'education',  label: 'Education',    icon: GraduationCap, color: 'text-blue-400'   },
  { id: 'house',      label: 'House',        icon: Home,          color: 'text-amber-400'  },
  { id: 'vehicle',    label: 'Vehicle',      icon: Car,           color: 'text-green-400'  },
  { id: 'travel',     label: 'Travel',       icon: Plane,         color: 'text-cyan-400'   },
  { id: 'wedding',    label: 'Wedding',      icon: Heart,         color: 'text-pink-400'   },
  { id: 'wealth',     label: 'Wealth',       icon: TrendingUp,    color: 'text-emerald-400'},
  { id: 'emergency',  label: 'Emergency',    icon: Shield,        color: 'text-red-400'    },
]

const DETAIL_TABS = [
  { id: 'progress',     label: 'Progress',    icon: Target    },
  { id: 'chart',        label: 'Growth Chart',icon: BarChart2  },
  { id: 'installments', label: 'Installments',icon: ListOrdered},
  { id: 'funds',        label: 'Funds',       icon: Briefcase  },
]

function fmtINR(n) {
  if (n == null) return '—'
  if (n >= 1e7)  return `₹${(n / 1e7).toFixed(1)}Cr`
  if (n >= 1e5)  return `₹${(n / 1e5).toFixed(1)}L`
  return `₹${(+n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

// ── Create Goal Drawer ────────────────────────────────────────────────────────

function CreateGoalDrawer({ onClose, onCreate }) {
  const [step,    setStep]    = useState(0)  // 0:type 1:details 2:sip 3:review
  const [saving,  setSaving]  = useState(false)
  const [err,     setErr]     = useState('')
  const [form, setForm] = useState({
    goal_type:       'wealth',
    name:            '',
    target_amount:   1000000,
    target_date:     '',
    monthly_sip:     5000,
    expected_return: 12,
    sip_date:        1,
    notes:           '',
  })

  const today = new Date()
  const minDate = new Date(today.getFullYear(), today.getMonth() + 1, 1).toISOString().slice(0, 10)

  const set = (k, v) => setForm(p => ({ ...p, [k]: v }))

  const validateStep = () => {
    if (step === 1) {
      if (!form.name.trim())        return 'Goal name is required'
      if (!form.target_amount || form.target_amount <= 0) return 'Target amount must be positive'
      if (!form.target_date)        return 'Target date is required'
    }
    if (step === 2) {
      if (form.monthly_sip < 0)    return 'Monthly SIP cannot be negative'
      if (form.expected_return <= 0) return 'Expected return must be positive'
    }
    return ''
  }

  const next = () => {
    const e = validateStep()
    if (e) { setErr(e); return }
    setErr('')
    setStep(s => s + 1)
  }

  const handleCreate = async () => {
    setSaving(true)
    setErr('')
    try {
      await onCreate({
        name:            form.name.trim(),
        goal_type:       form.goal_type,
        target_amount:   +form.target_amount,
        target_date:     form.target_date,
        monthly_sip:     +form.monthly_sip,
        expected_return: +form.expected_return,
        sip_date:        +form.sip_date,
        notes:           form.notes,
      })
      onClose()
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full sm:max-w-lg rounded-t-2xl sm:rounded-2xl border border-border p-6 space-y-5 glass-panel">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-slate-100 font-bold text-base">Create SIP Goal</h2>
            <p className="text-muted text-xs mt-0.5">Step {step + 1} of 4</p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-muted hover:text-slate-300 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>

        {/* Progress bar */}
        <div className="h-1 bg-surface rounded-full overflow-hidden">
          <div className="h-full bg-accent rounded-full transition-all duration-300" style={{ width: `${(step + 1) * 25}%` }} />
        </div>

        {/* Step 0: Goal type */}
        {step === 0 && (
          <div>
            <p className="text-muted text-xs mb-3">What are you saving for?</p>
            <div className="grid grid-cols-4 gap-2">
              {GOAL_TYPES.map(g => {
                const Icon = g.icon
                return (
                  <button
                    key={g.id}
                    onClick={() => set('goal_type', g.id)}
                    className={`flex flex-col items-center gap-1.5 p-3 rounded-xl border transition-all ${
                      form.goal_type === g.id ? 'border-accent bg-accent/10' : 'border-border hover:border-accent/40'
                    }`}
                  >
                    <Icon size={18} className={form.goal_type === g.id ? 'text-accent' : g.color} />
                    <span className="text-[10px] text-muted">{g.label}</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {/* Step 1: Goal details */}
        {step === 1 && (
          <div className="space-y-4">
            <div className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">Goal Name</label>
              <input
                value={form.name}
                onChange={e => set('name', e.target.value)}
                placeholder={`e.g. My ${GOAL_TYPES.find(g => g.id === form.goal_type)?.label} Fund`}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-muted text-[10px] uppercase tracking-widest">Target Amount (₹)</label>
                <input
                  type="number" min="1000" step="10000" value={form.target_amount}
                  onChange={e => set('target_amount', e.target.value)}
                  className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
                />
              </div>
              <div className="space-y-1">
                <label className="text-muted text-[10px] uppercase tracking-widest">Target Date</label>
                <input
                  type="date" min={minDate} value={form.target_date}
                  onChange={e => set('target_date', e.target.value)}
                  className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
                />
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-muted text-[10px] uppercase tracking-widest">Notes (optional)</label>
              <input
                value={form.notes}
                onChange={e => set('notes', e.target.value)}
                placeholder="Any notes about this goal…"
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
              />
            </div>
          </div>
        )}

        {/* Step 2: SIP setup */}
        {step === 2 && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-muted text-[10px] uppercase tracking-widest">Monthly SIP (₹)</label>
                <input
                  type="number" min="0" step="500" value={form.monthly_sip}
                  onChange={e => set('monthly_sip', e.target.value)}
                  className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
                />
              </div>
              <div className="space-y-1">
                <label className="text-muted text-[10px] uppercase tracking-widest">Expected Return (%/yr)</label>
                <input
                  type="number" min="1" max="50" step="0.5" value={form.expected_return}
                  onChange={e => set('expected_return', e.target.value)}
                  className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
                />
              </div>
              <div className="space-y-1">
                <label className="text-muted text-[10px] uppercase tracking-widest">SIP Date (1–28)</label>
                <input
                  type="number" min="1" max="28" value={form.sip_date}
                  onChange={e => set('sip_date', e.target.value)}
                  className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
                />
              </div>
            </div>
          </div>
        )}

        {/* Step 3: Review */}
        {step === 3 && (
          <div className="rounded-xl border border-border p-4 space-y-3" style={{ background: '#0a0f1c' }}>
            <p className="text-slate-100 font-semibold text-sm">Review Your Goal</p>
            {[
              ['Type',          GOAL_TYPES.find(g => g.id === form.goal_type)?.label],
              ['Name',          form.name],
              ['Target',        fmtINR(+form.target_amount)],
              ['Target Date',   form.target_date],
              ['Monthly SIP',   fmtINR(+form.monthly_sip)],
              ['Expected Ret.', `${form.expected_return}% p.a.`],
              ['SIP Date',      `${form.sip_date}th of month`],
            ].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between text-sm">
                <span className="text-muted text-xs">{k}</span>
                <span className="text-slate-200 font-medium">{v}</span>
              </div>
            ))}
          </div>
        )}

        {err && <p className="text-loss text-xs">{err}</p>}

        {/* Actions */}
        <div className="flex gap-3">
          {step > 0 && (
            <button
              onClick={() => { setStep(s => s - 1); setErr('') }}
              className="px-4 py-2.5 bg-surface hover:bg-white/5 text-muted rounded-lg text-sm transition-colors"
            >
              Back
            </button>
          )}
          {step < 3 ? (
            <button
              onClick={next}
              className="flex-1 py-2.5 bg-accent hover:bg-accent/90 text-white rounded-lg text-sm font-semibold transition-colors"
            >
              Next
            </button>
          ) : (
            <button
              onClick={handleCreate} disabled={saving}
              className="flex-1 py-2.5 bg-accent hover:bg-accent/90 disabled:opacity-50 text-white rounded-lg text-sm font-semibold transition-colors"
            >
              {saving ? 'Creating Goal…' : 'Create Goal'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SIPTracker() {
  const {
    goals, activeGoal, loading, detailLoading,
    loadGoals, loadGoalDetail,
    createGoal, updateGoal, deleteGoal,
    addFund, removeFund,
    addInstallment, getInstallments, getProjection, refreshNavs,
    runCalculator, calcRequiredSIP, calcTimeToTarget,
    searchFunds,
  } = useSIPTracker()

  const [selectedGoalId, setSelectedGoalId] = useState(null)
  const [detailTab,      setDetailTab]      = useState('progress')
  const [showCreate,     setShowCreate]     = useState(false)
  const [showCalc,       setShowCalc]       = useState(false)
  const [refreshing,     setRefreshing]     = useState(false)
  const [confirmDel,     setConfirmDel]     = useState(null)

  useEffect(() => { loadGoals() }, [])

  const handleSelectGoal = async (id) => {
    setSelectedGoalId(id)
    setDetailTab('progress')
    await loadGoalDetail(id)
  }

  const handleDelete = async (id) => {
    setConfirmDel(null)
    await deleteGoal(id)
    if (selectedGoalId === id) setSelectedGoalId(null)
  }

  const handleRefresh = async () => {
    if (!selectedGoalId) return
    setRefreshing(true)
    try { await refreshNavs(selectedGoalId) }
    finally { setRefreshing(false) }
  }

  // Summary stats
  const totalInvested    = goals.reduce((s, g) => s + (g.total_invested  || 0), 0)
  const totalValue       = goals.reduce((s, g) => s + (g.current_value   || 0), 0)
  const totalMonthlySIP  = goals.reduce((s, g) => s + (g.monthly_sip     || 0), 0)

  // Fund list for the selected goal
  const [sipFunds, setSipFunds] = useState([])
  useEffect(() => {
    if (!selectedGoalId) { setSipFunds([]); return }
    apiFetch(`/api/v1/sip/goals/${selectedGoalId}/funds-list`)
      .then(r => r.ok ? r.json() : [])
      .then(data => setSipFunds(Array.isArray(data) ? data : []))
      .catch(() => setSipFunds([]))
  }, [selectedGoalId, activeGoal])

  return (
    <div className="space-y-4 fade-in">
      {/* ── Header ── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-slate-100 font-bold text-lg flex items-center gap-2">
            <Target size={18} className="text-accent" />
            SIP Tracker & Goal Planner
          </h1>
          <p className="text-muted text-xs mt-0.5">Track SIP investments towards your financial goals</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowCalc(!showCalc)}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold border transition-colors ${
              showCalc ? 'border-accent bg-accent/10 text-accent' : 'border-border glass-panel text-muted hover:text-accent hover:border-accent/40'
            }`}
          >
            <Calculator size={13} /> SIP Calculator
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-4 py-2 bg-accent hover:bg-accent/90 text-white rounded-xl text-xs font-semibold transition-colors"
          >
            <Plus size={13} /> New Goal
          </button>
        </div>
      </div>

      {/* ── Summary strip ── */}
      {goals.length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Total Invested', value: fmtINR(totalInvested) },
            { label: 'Current Value',  value: fmtINR(totalValue),    color: 'text-profit' },
            { label: 'Monthly SIPs',   value: fmtINR(totalMonthlySIP), color: 'text-accent' },
          ].map(m => (
            <div key={m.label} className="rounded-xl border border-border px-4 py-3 glass-panel">
              <p className="text-muted text-[9px] uppercase tracking-widest">{m.label}</p>
              <p className={`font-bold text-lg tabular-nums mt-0.5 ${m.color || 'text-slate-100'}`}>{m.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* ── SIP Calculator (expandable) ── */}
      {showCalc && (
        <div className="rounded-xl border border-border p-5 glass-panel">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-slate-100 font-semibold text-sm flex items-center gap-2">
              <Calculator size={14} className="text-cyan-400" /> SIP Calculator
            </h2>
            <button onClick={() => setShowCalc(false)} className="text-muted hover:text-slate-300">
              <X size={14} />
            </button>
          </div>
          <SIPCalculator
            runCalculator={runCalculator}
            calcRequiredSIP={calcRequiredSIP}
            calcTimeToTarget={calcTimeToTarget}
          />
        </div>
      )}

      {/* ── Two-column layout ── */}
      {loading ? <LoadingSpinner /> : (
        <div className="flex gap-4">
          {/* Goals sidebar */}
          <div className="w-72 flex-shrink-0 space-y-2">
            {goals.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border p-8 flex flex-col items-center gap-2 text-center">
                <Target size={28} className="text-muted/40" />
                <p className="text-muted text-sm font-medium">No goals yet</p>
                <p className="text-muted/50 text-xs">Create your first financial goal</p>
                <button
                  onClick={() => setShowCreate(true)}
                  className="mt-2 px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent rounded-lg text-xs font-semibold transition-colors"
                >
                  Create Goal
                </button>
              </div>
            ) : (
              goals.map(g => (
                <GoalCard
                  key={g.id}
                  goal={g}
                  isActive={selectedGoalId === g.id}
                  onClick={() => handleSelectGoal(g.id)}
                  onDelete={id => setConfirmDel(id)}
                />
              ))
            )}
          </div>

          {/* Detail panel */}
          <div className="flex-1 min-w-0">
            {!selectedGoalId ? (
              <div className="rounded-xl border border-border flex flex-col items-center justify-center h-80 gap-3 glass-panel">
                <Target size={32} className="text-muted/30" />
                <p className="text-muted text-sm">Select a goal to view details</p>
              </div>
            ) : detailLoading ? (
              <div className="rounded-xl border border-border flex items-center justify-center h-80 glass-panel">
                <LoadingSpinner />
              </div>
            ) : activeGoal ? (
              <div className="rounded-xl border border-border overflow-hidden glass-panel">
                {/* Goal header */}
                <div className="px-5 py-4 border-b border-border">
                  <h2 className="text-slate-100 font-bold text-base">{activeGoal.goal_name}</h2>
                  <p className="text-muted text-xs mt-0.5 capitalize">
                    {activeGoal.goal_type} · Target: {fmtINR(activeGoal.target_amount)} by {activeGoal.target_date}
                  </p>
                </div>

                {/* Tabs */}
                <div className="flex border-b border-border px-4 gap-0.5">
                  {DETAIL_TABS.map(t => {
                    const Icon = t.icon
                    return (
                      <button
                        key={t.id}
                        onClick={() => setDetailTab(t.id)}
                        className={`flex items-center gap-1.5 px-3 py-3 text-xs font-semibold border-b-2 transition-colors ${
                          detailTab === t.id
                            ? 'border-accent text-accent'
                            : 'border-transparent text-muted hover:text-slate-300'
                        }`}
                      >
                        <Icon size={11} /> {t.label}
                      </button>
                    )
                  })}
                </div>

                {/* Tab content */}
                <div className="p-5">
                  {detailTab === 'progress' && (
                    <GoalProgressPanel
                      goal={activeGoal}
                      onRefresh={handleRefresh}
                      refreshing={refreshing}
                    />
                  )}
                  {detailTab === 'chart' && (
                    <GoalGrowthChart
                      goalId={selectedGoalId}
                      targetAmount={activeGoal.target_amount}
                      getProjection={getProjection}
                    />
                  )}
                  {detailTab === 'installments' && (
                    <InstallmentLog
                      goalId={selectedGoalId}
                      funds={sipFunds}
                      getInstallments={getInstallments}
                      addInstallment={addInstallment}
                    />
                  )}
                  {detailTab === 'funds' && (
                    <FundManager
                      goalId={selectedGoalId}
                      funds={sipFunds}
                      searchFunds={searchFunds}
                      addFund={addFund}
                      removeFund={removeFund}
                    />
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}

      {/* ── Create goal drawer ── */}
      {showCreate && (
        <CreateGoalDrawer
          onClose={() => setShowCreate(false)}
          onCreate={createGoal}
        />
      )}

      {/* ── Delete confirmation ── */}
      {confirmDel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="rounded-2xl border border-border p-6 space-y-4 max-w-sm w-full mx-4 glass-panel">
            <h3 className="text-slate-100 font-bold">Delete Goal?</h3>
            <p className="text-muted text-sm">This will permanently delete the goal and all its installment history. This cannot be undone.</p>
            <div className="flex gap-3">
              <button
                onClick={() => handleDelete(confirmDel)}
                className="flex-1 py-2.5 bg-red-500/20 hover:bg-red-500/30 text-red-400 rounded-lg text-sm font-semibold transition-colors"
              >
                Delete
              </button>
              <button
                onClick={() => setConfirmDel(null)}
                className="flex-1 py-2.5 bg-surface hover:bg-white/5 text-muted rounded-lg text-sm transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
