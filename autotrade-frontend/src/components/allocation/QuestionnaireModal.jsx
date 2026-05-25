import { useState } from 'react'
import { X, ChevronRight, RotateCcw } from 'lucide-react'
import AllocationDonut from './AllocationDonut'

const QUESTIONS = [
  {
    id: 'q1_horizon',
    question: 'How long can you stay invested without needing the money?',
    options: [
      { value: 1, label: '< 2 years',  sub: 'Short term' },
      { value: 2, label: '2–5 years',  sub: 'Medium term' },
      { value: 3, label: '5–10 years', sub: 'Long term' },
      { value: 4, label: '10+ years',  sub: 'Very long term' },
    ],
  },
  {
    id: 'q2_reaction',
    question: 'If your portfolio drops 20% in a market crash, you would:',
    options: [
      { value: 1, label: 'Sell everything', sub: "Protect what's left" },
      { value: 2, label: 'Sell some',       sub: 'Reduce exposure' },
      { value: 3, label: 'Hold steady',     sub: 'Wait it out' },
      { value: 4, label: 'Buy more',        sub: 'Opportunity!' },
    ],
  },
  {
    id: 'q3_goal',
    question: 'Your primary investment goal is:',
    options: [
      { value: 1, label: 'Protect capital', sub: "Don't lose money" },
      { value: 2, label: 'Regular income',  sub: 'Steady returns' },
      { value: 3, label: 'Wealth growth',   sub: 'Beat inflation' },
      { value: 4, label: 'High returns',    sub: 'Maximum growth' },
    ],
  },
  {
    id: 'q4_income',
    question: 'How stable is your income?',
    options: [
      { value: 1, label: 'Irregular',    sub: 'Freelance/gig work' },
      { value: 2, label: 'Variable',     sub: 'Sales/commission' },
      { value: 3, label: 'Stable job',   sub: 'Salaried professional' },
      { value: 4, label: 'Very stable',  sub: 'Government/senior role' },
    ],
  },
  {
    id: 'q5_experience',
    question: 'Your investment experience?',
    options: [
      { value: 1, label: 'Never invested',     sub: 'Starting fresh' },
      { value: 2, label: 'FDs only',           sub: 'Safe instruments' },
      { value: 3, label: 'Some mutual funds',  sub: 'A few years' },
      { value: 4, label: 'Active investor',    sub: 'Stocks + MFs + more' },
    ],
  },
]

const PROFILE_NAMES = {
  conservative:          'Conservative',
  moderate_conservative: 'Moderate Conservative',
  moderate:              'Moderate',
  moderate_aggressive:   'Moderate Aggressive',
  aggressive:            'Aggressive',
  very_aggressive:       'Very Aggressive',
}

const PROFILE_COLORS = {
  conservative:          '#10B981',
  moderate_conservative: '#06B6D4',
  moderate:              '#3B82F6',
  moderate_aggressive:   '#F59E0B',
  aggressive:            '#F97316',
  very_aggressive:       '#EF4444',
}

export default function QuestionnaireModal({ isOpen, onClose, onComplete }) {
  const [step,      setStep]      = useState(0)
  const [answers,   setAnswers]   = useState({})
  const [result,    setResult]    = useState(null)
  const [submitting,setSubmitting]= useState(false)

  if (!isOpen) return null

  const q = QUESTIONS[step]

  function handleSelect(value) {
    const newAnswers = { ...answers, [q.id]: value }
    setAnswers(newAnswers)

    if (step < QUESTIONS.length - 1) {
      setTimeout(() => setStep(step + 1), 220)
    } else {
      submitAnswers(newAnswers)
    }
  }

  async function submitAnswers(a) {
    setSubmitting(true)
    try {
      const res  = await fetch('/api/v1/allocation/risk-profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(a),
      })
      const data = await res.json()
      setResult(data)
    } catch { }
    setSubmitting(false)
  }

  function reset() {
    setStep(0)
    setAnswers({})
    setResult(null)
  }

  function handleApply() {
    if (result) onComplete?.(result)
  }

  const color = result ? PROFILE_COLORS[result.profile] || '#3B82F6' : '#3B82F6'

  // Convert allocation to donut format
  const targetDonutAlloc = result ? Object.fromEntries(
    Object.entries(result.recommended_allocation || {}).map(([k, v]) => [k, { value: v, total_pct: v }])
  ) : {}

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.7)' }}>
      <div className="w-full max-w-md rounded-2xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <p className="text-slate-100 font-semibold text-sm">Risk Profile Assessment</p>
          <button onClick={onClose} className="text-muted hover:text-white"><X size={16} /></button>
        </div>

        {!result ? (
          <div className="p-5 space-y-5">
            {/* Progress bar */}
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] text-muted">
                <span>Question {step + 1} of {QUESTIONS.length}</span>
                <span>{Math.round(((step) / QUESTIONS.length) * 100)}% done</span>
              </div>
              <div className="h-1.5 bg-surface rounded-full overflow-hidden">
                <div className="h-full bg-accent rounded-full transition-all duration-300" style={{ width: `${(step / QUESTIONS.length) * 100}%` }} />
              </div>
            </div>

            <p className="text-slate-100 font-semibold text-sm leading-snug">{q.question}</p>

            <div className="grid grid-cols-2 gap-2">
              {q.options.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => handleSelect(opt.value)}
                  className={`p-3 rounded-xl border text-left transition-all ${
                    answers[q.id] === opt.value
                      ? 'border-accent/60 bg-accent/10 text-slate-100'
                      : 'border-border text-muted hover:border-accent/30 hover:text-slate-300'
                  }`}
                >
                  <p className="text-sm font-semibold">{opt.label}</p>
                  <p className="text-[10px] text-muted mt-0.5">{opt.sub}</p>
                </button>
              ))}
            </div>
          </div>
        ) : (
          /* Result screen */
          <div className="p-5 space-y-5">
            <div className="text-center space-y-2">
              <div className="text-4xl font-bold" style={{ color }}>
                {PROFILE_NAMES[result.profile] || result.profile}
              </div>
              <p className="text-muted text-sm">{result.description}</p>
              <p className="text-muted/60 text-xs">{result.suitable_for}</p>
            </div>

            <div className="flex justify-center">
              <AllocationDonut
                allocation={targetDonutAlloc}
                size="md"
              />
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs text-center">
              <div className="rounded-xl border border-border p-3" style={{ background: '#0a0f1c' }}>
                <p className="text-muted text-[10px]">Expected CAGR</p>
                <p className="text-slate-200 font-bold text-base mt-0.5">{result.cagr_range}</p>
              </div>
              <div className="rounded-xl border border-border p-3" style={{ background: '#0a0f1c' }}>
                <p className="text-muted text-[10px]">Time Horizon</p>
                <p className="text-slate-200 font-bold text-base mt-0.5">{result.horizon}</p>
              </div>
            </div>

            <div className="flex gap-2">
              <button
                onClick={handleApply}
                className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent text-white text-sm font-semibold hover:bg-accent/90 transition-colors"
              >
                Apply This Profile <ChevronRight size={14} />
              </button>
              <button onClick={reset} className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl border border-border text-muted hover:text-slate-300 text-xs transition-colors">
                <RotateCcw size={12} /> Retake
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
