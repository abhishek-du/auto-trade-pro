import { useState, useRef } from 'react'
import { Stethoscope, RefreshCw, AlertTriangle, CheckCircle, Info, BriefcaseMedical } from 'lucide-react'
import { usePortfolioTracker } from '../hooks/usePortfolioTracker'
import { usePortfolioDoctor } from '../hooks/usePortfolioDoctor'
import PortfolioSelector from '../components/portfolio/PortfolioSelector'
import HealthScoreCard from '../components/doctor/HealthScoreCard'
import FindingCard from '../components/doctor/FindingCard'
import ScoreHistory from '../components/doctor/ScoreHistory'
import DiagnosisSettings from '../components/doctor/DiagnosisSettings'
import ProgressOverlay from '../components/doctor/ProgressOverlay'

const MODULE_FILTERS = ['All', 'CONCENTRATION', 'RISK_QUALITY', 'DIVERSIFICATION', 'TAX_EFFICIENCY', 'PERFORMANCE', 'SECTOR_TIMING', 'POSITION_SIZING']

const MODULE_LABELS = {
  All: 'All',
  CONCENTRATION:   'Concentration',
  RISK_QUALITY:    'Risk',
  DIVERSIFICATION: 'Diversification',
  TAX_EFFICIENCY:  'Tax',
  PERFORMANCE:     'Performance',
  SECTOR_TIMING:   'Sectors',
  POSITION_SIZING: 'Sizing',
}

const SEV_ORDER = { CRITICAL: 0, WARNING: 1, INFO: 2, GOOD: 3 }

function SeveritySection({ label, color, borderColor, findings, defaultExpanded }) {
  const [collapsed, setCollapsed] = useState(!defaultExpanded)
  if (!findings.length) return null
  return (
    <div className="space-y-2">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center gap-2 w-full text-left"
      >
        <div className={`h-px flex-1 ${color.replace('text-', 'bg-')}/20`} />
        <span className={`${color} text-xs font-bold uppercase tracking-widest shrink-0 px-2`}>
          {label} ({findings.length})
        </span>
        <div className={`h-px flex-1 ${color.replace('text-', 'bg-')}/20`} />
        <span className={`${color} text-xs ml-1`}>{collapsed ? '▼' : '▲'}</span>
      </button>
      {!collapsed && (
        <div className="space-y-2">
          {findings.map((f, i) => (
            <FindingCard key={`${f.module}-${f.title}-${i}`} finding={f} defaultExpanded={f.severity === 'CRITICAL'} />
          ))}
        </div>
      )}
    </div>
  )
}

function ActionTracker({ findings }) {
  const [checked, setChecked] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem('doctor-actions') || '[]')) }
    catch { return new Set() }
  })

  const allActions = findings.flatMap(f =>
    (f.actions || []).map((a, i) => ({ action: a, module: f.module, title: f.title, key: `${f.module}-${i}-${a.slice(0, 20)}` }))
  )

  function toggle(key) {
    setChecked(prev => {
      const n = new Set(prev)
      n.has(key) ? n.delete(key) : n.add(key)
      try { localStorage.setItem('doctor-actions', JSON.stringify([...n])) } catch {}
      return n
    })
  }

  if (!allActions.length) return null
  const done = [...checked].filter(k => allActions.some(a => a.key === k)).length

  return (
    <div className="rounded-xl border border-border glass-panel">
      <div className="px-5 py-3.5 border-b border-border flex items-center justify-between">
        <h3 className="text-slate-100 font-semibold text-sm">Your Action Plan</h3>
        <span className="text-xs text-muted">{done} of {allActions.length} completed</span>
      </div>
      <div className="p-4 space-y-1.5 max-h-64 overflow-y-auto">
        {allActions.map(({ action, module, key }) => (
          <label key={key} className="flex items-start gap-3 cursor-pointer group">
            <input
              type="checkbox"
              checked={checked.has(key)}
              onChange={() => toggle(key)}
              className="mt-0.5 accent-cyan shrink-0"
            />
            <div className="flex-1">
              <span className={`text-xs ${checked.has(key) ? 'line-through text-muted/60' : 'text-slate-300 group-hover:text-slate-100'}`}>
                {action}
              </span>
              <span className="text-muted/60 text-[9px] ml-2 uppercase tracking-wide">{MODULE_LABELS[module] || module}</span>
            </div>
          </label>
        ))}
      </div>
      {done > 0 && (
        <div className="px-4 pb-3">
          <div className="h-1.5 rounded-full bg-border overflow-hidden">
            <div
              className="h-full rounded-full bg-profit transition-all"
              style={{ width: `${(done / allActions.length) * 100}%` }}
            />
          </div>
        </div>
      )}
    </div>
  )
}

export default function PortfolioDoctor() {
  const {
    portfolios, activeId, setActiveId,
    createPortfolio, deletePortfolio,
  } = usePortfolioTracker()

  const {
    diagnosis, history, loading, progress, error,
    riskProfile, setRiskProfile,
    annualIncome, setAnnualIncome,
    runDiagnosis,
    criticalCount, warningCount, goodCount,
  } = usePortfolioDoctor(activeId)

  const [filterModule, setFilterModule] = useState('All')
  const findingsRef = useRef(null)

  function scrollToFindings() {
    findingsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const findings     = diagnosis?.findings || []
  const filtered     = filterModule === 'All' ? findings : findings.filter(f => f.module === filterModule)
  const critical     = filtered.filter(f => f.severity === 'CRITICAL')
  const warnings     = filtered.filter(f => f.severity === 'WARNING')
  const infos        = filtered.filter(f => f.severity === 'INFO')
  const goods        = filtered.filter(f => f.severity === 'GOOD')

  return (
    <div className="space-y-6 fade-in">
      <ProgressOverlay loading={loading} progress={progress} />

      {/* ── Header ── */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl" style={{ background: 'rgba(239,68,68,0.12)' }}>
            <Stethoscope size={20} className="text-red-400" />
          </div>
          <div>
            <h1 className="text-slate-100 font-bold text-xl">Portfolio Doctor</h1>
            <p className="text-muted text-sm">AI-powered portfolio health analysis</p>
          </div>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <PortfolioSelector
            portfolios={portfolios}
            activeId={activeId}
            onSelect={setActiveId}
            onCreate={createPortfolio}
            onDelete={deletePortfolio}
          />
          <DiagnosisSettings
            riskProfile={riskProfile}
            setRiskProfile={setRiskProfile}
            annualIncome={annualIncome}
            setAnnualIncome={setAnnualIncome}
          />
          <button
            onClick={runDiagnosis}
            disabled={loading || !activeId}
            className="flex items-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)', color: 'white' }}
          >
            {loading
              ? <><RefreshCw size={14} className="animate-spin" /> Diagnosing…</>
              : <><Stethoscope size={14} /> Run Diagnosis</>
            }
          </button>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-5 py-4 flex items-center gap-3">
          <AlertTriangle size={16} className="text-red-400 shrink-0" />
          <p className="text-red-300 text-sm flex-1">{error}</p>
          <button onClick={runDiagnosis} className="text-xs text-red-400 border border-red-500/30 px-3 py-1.5 rounded-lg hover:bg-red-500/10">
            Retry
          </button>
        </div>
      )}

      {/* ── No diagnosis state ── */}
      {!diagnosis && !loading && (
        <div className="rounded-2xl border border-border py-16 flex flex-col items-center gap-4 glass-panel">
          <div className="p-4 rounded-2xl" style={{ background: 'rgba(239,68,68,0.08)' }}>
            <BriefcaseMedical size={40} className="text-red-400/50" />
          </div>
          <div className="text-center">
            <p className="text-slate-300 font-semibold text-base">No diagnosis yet</p>
            <p className="text-muted text-sm mt-1 max-w-xs">
              Click "Run Diagnosis" to get a complete AI analysis of your portfolio health, tax efficiency, and actionable recommendations
            </p>
          </div>
          <button
            onClick={runDiagnosis}
            disabled={!activeId}
            className="mt-2 flex items-center gap-2 px-6 py-3 rounded-xl font-bold text-sm disabled:opacity-40"
            style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)', color: 'white' }}
          >
            <Stethoscope size={16} /> Run Full Diagnosis
          </button>
        </div>
      )}

      {/* ── Results ── */}
      {diagnosis && (
        <div className="space-y-6">
          {/* Score + History */}
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <div className="md:col-span-3">
              <HealthScoreCard
                score={diagnosis.overall_score}
                grade={diagnosis.overall_grade}
                summary={diagnosis.summary}
                generatedAt={diagnosis.generated_at}
                quickWins={diagnosis.quick_wins || []}
                onQuickWinClick={scrollToFindings}
              />
            </div>
            <div className="md:col-span-2">
              <ScoreHistory history={history} />
            </div>
          </div>

          {/* Critical banner */}
          {criticalCount > 0 && (
            <div className="flex items-center gap-3 rounded-xl border border-red-500/30 bg-red-500/8 px-5 py-3.5">
              <AlertTriangle size={16} className="text-red-400 shrink-0" />
              <p className="text-red-300 text-sm font-semibold flex-1">
                {criticalCount} critical issue{criticalCount > 1 ? 's' : ''} need immediate attention
              </p>
              <button onClick={scrollToFindings} className="text-xs text-red-400 border border-red-500/30 px-3 py-1.5 rounded-lg hover:bg-red-500/10">
                View issues
              </button>
            </div>
          )}

          {/* Module filter */}
          <div className="flex items-center gap-2 flex-wrap" ref={findingsRef}>
            {MODULE_FILTERS.map(m => (
              <button
                key={m}
                onClick={() => setFilterModule(m)}
                className={`text-xs px-3 py-1.5 rounded-lg font-semibold border transition-all ${
                  filterModule === m
                    ? 'bg-accent/20 border-accent/40 text-cyan'
                    : 'border-border text-muted hover:text-slate-200 hover:bg-white/5'
                }`}
              >
                {MODULE_LABELS[m]}
              </button>
            ))}
            <div className="ml-auto flex items-center gap-2">
              {criticalCount > 0 && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-500/20 text-red-400">Critical ({criticalCount})</span>}
              {warningCount  > 0 && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400">Warnings ({warningCount})</span>}
              {goodCount     > 0 && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400">Good ({goodCount})</span>}
            </div>
          </div>

          {/* Findings */}
          <div className="space-y-4">
            <SeveritySection label="Critical Issues"        color="text-red-400"     borderColor="border-red-500"     findings={critical} defaultExpanded />
            <SeveritySection label="Warnings"              color="text-amber-400"   borderColor="border-amber-500"   findings={warnings} defaultExpanded />
            <SeveritySection label="Informational"         color="text-blue-400"    borderColor="border-blue-500"    findings={infos}    defaultExpanded={false} />
            <SeveritySection label="What's Working Well"   color="text-emerald-400" borderColor="border-emerald-500" findings={goods}    defaultExpanded={false} />
          </div>

          {/* AI Narrative */}
          {diagnosis.ai_narrative && (
            <div className="rounded-xl border border-border overflow-hidden glass-panel">
              <div className="px-5 py-3.5 border-b border-border flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-full bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center text-white text-xs font-bold">A</div>
                  <h3 className="text-slate-100 font-semibold text-sm">Dr. Arjun's Assessment</h3>
                </div>
                <span className={`text-[9px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full ${
                  diagnosis.is_ai_generated
                    ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                    : 'bg-slate-500/15 text-muted border border-border'
                }`}>
                  {diagnosis.is_ai_generated ? 'AI Analysis' : 'Rule-Based'}
                </span>
              </div>
              <div className="px-5 py-4 space-y-3">
                {diagnosis.ai_narrative.split('\n').filter(Boolean).map((para, i) => (
                  <p key={i} className="text-slate-300 text-sm leading-relaxed">{para}</p>
                ))}
                <p className="text-muted/50 text-[10px] pt-1 border-t border-border">
                  {diagnosis.is_ai_generated
                    ? 'Generated by Groq llama-3.1-8b-instant. '
                    : ''}
                  AI analysis is educational, not financial advice. Consult a SEBI-registered advisor.
                </p>
              </div>
            </div>
          )}

          {/* Action Tracker */}
          <ActionTracker findings={findings} />
        </div>
      )}
    </div>
  )
}
