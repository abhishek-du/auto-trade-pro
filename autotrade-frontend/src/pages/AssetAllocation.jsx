import { useState, useEffect } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { PieChart as PieIcon, RefreshCw, ClipboardList, Sliders, Info } from 'lucide-react'
import { useAllocation } from '../hooks/useAllocation'
import { usePortfolioTracker } from '../hooks/usePortfolioTracker'
import { useSIPTracker } from '../hooks/useSIPTracker'
import LoadingSpinner from '../components/LoadingSpinner'
import RiskProfileSelector  from '../components/allocation/RiskProfileSelector'
import AllocationDonut      from '../components/allocation/AllocationDonut'
import AllocationBars       from '../components/allocation/AllocationBars'
import RebalancingCards     from '../components/allocation/RebalancingCards'
import HoldingsBreakdown    from '../components/allocation/HoldingsBreakdown'
import RiskScoreGauge       from '../components/allocation/RiskScoreGauge'
import QuestionnaireModal   from '../components/allocation/QuestionnaireModal'
import CustomTargetEditor   from '../components/allocation/CustomTargetEditor'
import { formatINR }        from '../utils/indianFormat'

const TABS = [
  { id: 'overview',    label: 'Overview',            icon: PieIcon      },
  { id: 'rebalancing', label: 'Rebalancing',          icon: RefreshCw    },
  { id: 'holdings',   label: 'Holdings Breakdown',   icon: ClipboardList },
]

const THRESHOLD_OPTIONS = [3, 5, 10, 15]

export default function AssetAllocation() {
  const [searchParams] = useSearchParams()
  const urlPortfolio   = searchParams.get('portfolio')

  const { portfolios, activeId } = usePortfolioTracker()
  const portfolioId = urlPortfolio || activeId

  const { goals, loadGoals } = useSIPTracker()
  useEffect(() => { loadGoals() }, [loadGoals])
  const sipGoalIds = goals.map(g => g.id || g.goal_id).filter(Boolean)

  const {
    analysis, loading, error, loadAnalysis,
    riskProfile, setRiskProfile,
    rebalancingThreshold, setRebalancingThreshold,
    newInvestment, setNewInvestment,
    effectiveTarget, customTarget, applyCustomTarget, setCustomTarget,
    submitQuestionnaire,
  } = useAllocation(portfolioId, sipGoalIds)

  const [tab,              setTab]              = useState('overview')
  const [showQuestionnaire,setShowQuestionnaire]= useState(false)
  const [showCustomTarget, setShowCustomTarget] = useState(false)

  const activePortfolio = portfolios.find(p => p.id === portfolioId)
  const hasData = !!portfolioId

  const stocksVal = hasData ? Object.values(analysis?.current_allocation || {})
    .flatMap(c => c.holdings || [])
    .filter(h => h.type === 'stock')
    .reduce((s, h) => s + h.value, 0) : 0

  const mfVal = hasData ? Object.values(analysis?.current_allocation || {})
    .flatMap(c => c.holdings || [])
    .filter(h => h.type === 'mutual_fund')
    .reduce((s, h) => s + h.value, 0) : 0

  if (!hasData) return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 fade-in">
      <div className="w-20 h-20 rounded-full border-4 border-dashed border-border flex items-center justify-center">
        <PieIcon size={32} className="text-muted/30" />
      </div>
      <p className="text-slate-300 font-semibold text-lg">No investments to analyze</p>
      <p className="text-muted text-sm text-center max-w-xs">
        Add stocks to your portfolio or create SIP goals to see your asset allocation.
      </p>
      <div className="flex gap-3">
        <Link to="/portfolio-tracker" className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-semibold hover:bg-accent/90 transition-colors">
          Go to Portfolio
        </Link>
        <Link to="/sip" className="px-4 py-2 rounded-lg border border-border text-muted text-sm hover:text-slate-300 transition-colors">
          Go to SIP Tracker
        </Link>
      </div>
    </div>
  )

  return (
    <div className="space-y-5 fade-in">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <PieIcon size={18} className="text-cyan" />
            Asset Allocation
          </h1>
          <p className="text-muted text-sm mt-0.5">
            Stocks + Mutual Funds combined
            {activePortfolio && <span className="ml-2 text-muted/60">· {activePortfolio.name}</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowQuestionnaire(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-muted text-xs hover:text-slate-300 hover:border-accent/40 transition-colors"
          >
            <ClipboardList size={13} /> Take Risk Quiz
          </button>
          <button
            onClick={() => setShowCustomTarget(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-muted text-xs hover:text-slate-300 hover:border-accent/40 transition-colors"
          >
            <Sliders size={13} /> Customize Target
          </button>
          <button
            onClick={loadAnalysis}
            className="p-2 rounded-lg border border-border text-muted hover:text-white hover:border-accent/40 transition-colors"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Risk profile selector */}
      <RiskProfileSelector
        currentProfile={riskProfile}
        onSelect={setRiskProfile}
        recommendedProfile={analysis?.recommended_profile}
      />

      {/* Portfolio banner */}
      {analysis && (
        <div className="flex items-center gap-4 flex-wrap rounded-xl border border-border px-5 py-3 glass-panel">
          <div className="space-y-0.5">
            <p className="text-muted text-[10px] uppercase tracking-widest">Total Portfolio</p>
            <p className="text-slate-100 text-lg font-bold tabular-nums">{formatINR(analysis.portfolio_total, 0)}</p>
          </div>
          <div className="h-8 w-px bg-border" />
          <div className="space-y-0.5">
            <p className="text-muted text-[10px]">Stocks</p>
            <p className="text-slate-300 text-sm font-semibold tabular-nums">{formatINR(stocksVal, 0)}</p>
          </div>
          <div className="space-y-0.5">
            <p className="text-muted text-[10px]">Mutual Funds</p>
            <p className="text-slate-300 text-sm font-semibold tabular-nums">{formatINR(mfVal, 0)}</p>
          </div>
          <div className="ml-auto">
            <RiskScoreGauge riskScore={analysis.risk_score} />
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex items-center gap-0.5 glass-panel border border-border rounded-xl p-1 w-fit">
        {TABS.map(t => {
          const Icon = t.icon
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold transition-colors ${tab === t.id ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'}`}>
              <Icon size={12} /> {t.label}
            </button>
          )
        })}
      </div>

      {/* Content */}
      {loading ? (
        <LoadingSpinner message="Fetching current prices and computing allocation…" />
      ) : error ? (
        <div className="rounded-xl border border-red-500/20 px-5 py-4 text-red-400 text-sm" style={{ background: 'rgba(239,68,68,0.04)' }}>
          {error}
        </div>
      ) : analysis ? (
        <>
          {/* ── Overview tab ── */}
          {tab === 'overview' && (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              {/* Current donut */}
              <div className="rounded-xl border border-border p-5 flex flex-col items-center glass-panel">
                <p className="text-muted text-[10px] uppercase tracking-widest mb-3">Current Portfolio</p>
                <AllocationDonut
                  allocation={analysis.current_allocation}
                  size="lg"
                  targetAllocation={effectiveTarget}
                />
              </div>

              {/* Target donut */}
              <div className="rounded-xl border border-border p-5 flex flex-col items-center glass-panel">
                <p className="text-muted text-[10px] uppercase tracking-widest mb-3">
                  Target — {riskProfile.replace('_', ' ')}
                </p>
                <AllocationDonut
                  allocation={Object.fromEntries(
                    Object.entries(effectiveTarget).map(([k, v]) => [k, { value: v, total_pct: v }])
                  )}
                  size="lg"
                />
                {/* Risk comparison */}
                {analysis.risk_score && (
                  <div className="mt-4 w-full space-y-2 text-xs border-t border-border pt-3">
                    <div className="flex justify-between text-muted">
                      <span>Your risk score</span>
                      <span className="font-semibold" style={{ color: analysis.risk_score.color }}>
                        {analysis.risk_score.score} — {analysis.risk_score.label}
                      </span>
                    </div>
                    <div className="flex justify-between text-muted">
                      <span>Equity</span>
                      <span className="text-slate-300">{analysis.risk_score.equity_pct}%</span>
                    </div>
                    <div className="flex justify-between text-muted">
                      <span>Debt</span>
                      <span className="text-slate-300">{analysis.risk_score.debt_pct}%</span>
                    </div>
                    <div className="flex justify-between text-muted">
                      <span>Gold</span>
                      <span className="text-slate-300">{analysis.risk_score.gold_pct}%</span>
                    </div>
                  </div>
                )}
              </div>

              {/* Allocation bars */}
              <div className="rounded-xl border border-border p-5 glass-panel">
                <AllocationBars
                  current={analysis.current_allocation}
                  target={effectiveTarget}
                  showDeviation={true}
                  threshold={rebalancingThreshold}
                />
              </div>
            </div>
          )}

          {/* ── Rebalancing tab ── */}
          {tab === 'rebalancing' && (
            <div className="space-y-5">
              {/* Controls */}
              <div className="rounded-xl border border-border p-4 space-y-4 glass-panel">
                <div className="flex flex-wrap gap-6">
                  <div className="space-y-2">
                    <p className="text-muted text-[10px] uppercase tracking-widest">Suggest action when deviation exceeds</p>
                    <div className="flex gap-1.5">
                      {THRESHOLD_OPTIONS.map(t => (
                        <button key={t} onClick={() => setRebalancingThreshold(t)}
                          className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${rebalancingThreshold === t ? 'bg-accent/20 text-accent border border-accent/30' : 'bg-surface text-muted border border-border hover:text-slate-300'}`}>
                          {t}%
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              <RebalancingCards
                rebalancing={analysis.rebalancing}
                newInvestment={newInvestment}
                onNewInvestmentChange={setNewInvestment}
                threshold={rebalancingThreshold}
              />

              {/* Method info box */}
              <div className="rounded-xl border border-accent/20 px-5 py-4 space-y-2" style={{ background: 'rgba(59,130,246,0.05)' }}>
                <div className="flex items-center gap-2 text-accent text-sm font-semibold">
                  <Info size={14} />
                  Two ways to rebalance
                </div>
                <ol className="text-muted text-xs space-y-1 list-decimal list-inside">
                  <li>Direct new investments to underweight classes <span className="text-profit">(no tax impact)</span></li>
                  <li>Sell overweight assets, buy underweight <span className="text-amber-400">(may trigger capital gains)</span></li>
                </ol>
                <p className="text-muted/70 text-[10px]">
                  Tip: Use new SIP amounts first to rebalance gradually without selling and triggering taxes.
                </p>
              </div>
            </div>
          )}

          {/* ── Holdings breakdown tab ── */}
          {tab === 'holdings' && (
            <HoldingsBreakdown allocation={analysis.current_allocation} />
          )}
        </>
      ) : null}

      {/* Modals */}
      <QuestionnaireModal
        isOpen={showQuestionnaire}
        onClose={() => setShowQuestionnaire(false)}
        onComplete={async (result) => {
          setRiskProfile(result.profile)
          setShowQuestionnaire(false)
        }}
      />

      <CustomTargetEditor
        isOpen={showCustomTarget}
        currentTarget={effectiveTarget}
        onChange={(target) => { applyCustomTarget(target); setShowCustomTarget(false) }}
        onClose={() => setShowCustomTarget(false)}
      />
    </div>
  )
}
