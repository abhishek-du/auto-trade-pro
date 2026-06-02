import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Briefcase, Plus, RefreshCw, List, PieChart as PieIcon, Receipt, BarChart2, ExternalLink, Zap, Stethoscope } from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '../components/LoadingSpinner'
import { usePortfolioTracker } from '../hooks/usePortfolioTracker'
import PortfolioSelector from '../components/portfolio/PortfolioSelector'
import SummaryCards from '../components/portfolio/SummaryCards'
import HoldingsTable from '../components/portfolio/HoldingsTable'
import AddHoldingModal from '../components/portfolio/AddHoldingModal'
import SellModal from '../components/portfolio/SellModal'
import AllocationCharts from '../components/portfolio/AllocationCharts'
import TransactionsTab from '../components/portfolio/TransactionsTab'
import { formatINR } from '../utils/indianFormat'
import { apiFetch } from '../api/client'

function TaxQuickView({ portfolioId }) {
  const [status,  setStatus]  = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!portfolioId) return
    setLoading(true)
    apiFetch(`/api/v1/tax/current-fy-status/${portfolioId}`)
      .then(d => { setStatus(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [portfolioId])

  if (loading) return <LoadingSpinner message="Loading tax summary…" />

  return (
    <div className="space-y-4">
      {status && (
        <>
          <div className="flex items-center justify-between flex-wrap gap-2">
            <p className="text-slate-300 text-sm font-semibold">
              {status.financial_year} — Quick Tax Summary
            </p>
            <span className="text-muted text-xs">{status.days_left_in_fy}d left in FY</span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Realized STCG', value: status.realized_stcg,   color: 'text-amber-400', sub: '@ 20%' },
              { label: 'Realized LTCG', value: status.realized_ltcg,   color: 'text-blue-400',  sub: '@ 12.5%' },
              { label: 'Losses Booked', value: status.realized_losses,  color: 'text-profit',    sub: 'set-off' },
              { label: 'Est. Tax',      value: status.estimated_tax_so_far, color: 'text-red-400', sub: 'incl. cess' },
            ].map(c => (
              <div key={c.label} className="rounded-xl border border-border p-3 space-y-1" style={{ background: '#0a0f1c' }}>
                <p className="text-muted text-[10px] uppercase tracking-widest">{c.label}</p>
                <p className={`text-base font-bold tabular-nums ${c.color}`}>{formatINR(c.value, 0)}</p>
                <p className="text-muted/60 text-[10px]">{c.sub}</p>
              </div>
            ))}
          </div>

          {status.ltcg_exemption_remaining > 0 && (
            <div className="flex items-center gap-2 rounded-xl border border-profit/30 px-4 py-2.5 bg-profit/5">
              <Zap size={13} className="text-profit flex-shrink-0" />
              <p className="text-xs text-profit font-medium">
                {formatINR(status.ltcg_exemption_remaining, 0)} of ₹1.25L LTCG exemption still unused this year — consider harvesting gains
              </p>
            </div>
          )}
        </>
      )}

      <Link
        to={`/tax?portfolio=${portfolioId}`}
        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-accent/30 bg-accent/10 text-accent text-xs font-semibold hover:bg-accent/20 transition-colors"
      >
        <Receipt size={12} /> View Full Tax Calculator
        <ExternalLink size={11} className="text-accent/70" />
      </Link>
    </div>
  )
}

const TABS = [
  { id: 'holdings',     label: 'Holdings',     icon: List       },
  { id: 'allocation',   label: 'Allocation',   icon: PieIcon    },
  { id: 'tax',          label: 'Tax Summary',  icon: Receipt    },
  { id: 'transactions', label: 'Trade History', icon: BarChart2  },
  { id: 'doctor',       label: 'Doctor',        icon: Stethoscope },
]

export default function PortfolioTracker() {
  const {
    portfolios, activeId, setActiveId, detail, summary,
    loading, detailLoading, reload,
    createPortfolio, deletePortfolio,
    addHolding, sellHolding, deleteHolding,
    searchStocks, getTransactions,
  } = usePortfolioTracker()

  const [tab,            setTab]            = useState('holdings')
  const [showAdd,        setShowAdd]        = useState(false)
  const [sellTarget,     setSellTarget]     = useState(null)
  const [txSymbol,       setTxSymbol]       = useState(null)
  const [txData,         setTxData]         = useState([])
  const [txLoading,      setTxLoading]      = useState(false)
  const [quickCreate,    setQuickCreate]    = useState(false)
  const [quickName,      setQuickName]      = useState('')
  const [quickCreating,  setQuickCreating]  = useState(false)

  async function loadTransactions(symbol) {
    setTxLoading(true)
    try {
      const data = await getTransactions(symbol)
      setTxData(Array.isArray(data) ? data : [])
    } catch {
      setTxData([])
    } finally {
      setTxLoading(false)
    }
  }

  function handleTabChange(id) {
    setTab(id)
    if (id === 'transactions') loadTransactions(null)
  }

  async function handleDelete(holding) {
    if (!confirm(`Remove ${holding.symbol?.replace('.NS', '')} from portfolio? This cannot be undone.`)) return
    try {
      await deleteHolding(holding.id)
      toast.success('Holding removed')
    } catch {
      toast.error('Failed to remove')
    }
  }

  if (loading) return <LoadingSpinner message="Loading portfolios…" />

  const hasPortfolio = portfolios.length > 0
  const holdings     = detail?.holdings || []

  return (
    <div className="space-y-5 fade-in">

      {/* ── Header ── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <Briefcase size={18} className="text-cyan" />
            My Holdings
          </h1>
          <p className="text-muted text-sm mt-0.5">Track real stock holdings, live P&L and XIRR</p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <PortfolioSelector
            portfolios={portfolios}
            activeId={activeId}
            onSelect={setActiveId}
            onCreate={createPortfolio}
            onDelete={deletePortfolio}
          />
          {activeId && (
            <>
              <button
                onClick={reload}
                className="p-2 rounded-lg border border-border text-muted hover:text-white hover:border-accent/40 transition-colors"
                title="Refresh prices"
              >
                <RefreshCw size={14} className={detailLoading ? 'animate-spin' : ''} />
              </button>
              <button
                onClick={async () => {
                  try {
                    const r = await apiFetch('/api/v1/portfolios/sync-zerodha', { method: 'POST' })
                    if (!r.ok) {
                      const err = await r.json().catch(() => ({}))
                      toast.error(err.detail || 'Zerodha sync failed — connect Zerodha first')
                      return
                    }
                    const d = await r.json()
                    toast.success(`Synced ${d.synced} Zerodha holdings`)
                    reload()
                  } catch {
                    toast.error('Sync request failed')
                  }
                }}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-blue-500/30 bg-blue-500/8 text-blue-400 text-xs font-semibold hover:bg-blue-500/15 transition-colors"
                title="Pull live Zerodha Demat holdings into this view"
              >
                Sync Zerodha
              </button>
              <button
                onClick={() => setShowAdd(true)}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold hover:opacity-90 transition-opacity"
              >
                <Plus size={14} /> Add Stock
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── No portfolio state ── */}
      {!hasPortfolio && (
        <div className="bg-panel border border-border rounded-xl p-12 text-center space-y-4">
          <Briefcase size={40} className="text-muted/40 mx-auto" />
          <p className="text-slate-300 font-semibold">No portfolios yet</p>
          <p className="text-muted text-sm">Create your first portfolio to start tracking your holdings.</p>
          {quickCreate ? (
            <form
              className="flex items-center gap-2 justify-center"
              onSubmit={async (e) => {
                e.preventDefault()
                if (!quickName.trim()) return
                setQuickCreating(true)
                try {
                  await createPortfolio(quickName.trim())
                  setQuickCreate(false)
                  setQuickName('')
                } catch { toast.error('Failed to create') }
                finally { setQuickCreating(false) }
              }}
            >
              <input
                autoFocus
                value={quickName}
                onChange={e => setQuickName(e.target.value)}
                placeholder="Portfolio name…"
                className="px-3 py-2 rounded-lg border border-border bg-bg text-sm text-slate-200 placeholder-muted outline-none focus:border-accent/50"
              />
              <button
                type="submit"
                disabled={quickCreating || !quickName.trim()}
                className="px-4 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
              >
                {quickCreating ? 'Creating…' : 'Create'}
              </button>
              <button type="button" onClick={() => setQuickCreate(false)} className="px-3 py-2 text-muted text-sm hover:text-white">
                Cancel
              </button>
            </form>
          ) : (
            <button
              onClick={() => setQuickCreate(true)}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-500 text-white text-sm font-semibold"
            >
              <Plus size={14} /> Create Portfolio
            </button>
          )}
        </div>
      )}

      {/* ── Active portfolio content ── */}
      {activeId && (
        <>
          {/* Summary cards */}
          {detailLoading && !detail ? (
            <LoadingSpinner message="Loading portfolio…" />
          ) : (
            <SummaryCards summary={summary} />
          )}

          {/* Tab bar */}
          <div className="flex items-center gap-0.5 bg-panel border border-border rounded-xl p-1 w-fit">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => handleTabChange(id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  tab === id ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'
                }`}
              >
                <Icon size={12} />
                {label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="bg-panel border border-border rounded-xl p-4">
            {tab === 'holdings' && (
              <HoldingsTable
                holdings={holdings}
                onSell={h => setSellTarget(h)}
                onDelete={handleDelete}
              />
            )}

            {tab === 'allocation' && (
              detail?.allocation
                ? <AllocationCharts allocation={detail.allocation} />
                : <p className="text-muted text-sm text-center py-8">No holdings to chart.</p>
            )}

            {tab === 'tax' && (
              <TaxQuickView portfolioId={activeId} />
            )}

            {tab === 'transactions' && (
              txLoading ? (
                <LoadingSpinner message="Loading transactions…" />
              ) : (
                <TransactionsTab
                  transactions={txData}
                  onRefresh={() => loadTransactions(txSymbol)}
                />
              )
            )}

            {tab === 'doctor' && (
              <div className="rounded-xl border border-border p-5 space-y-4" style={{ background: '#0F1829' }}>
                <div className="flex items-center gap-2">
                  <Stethoscope size={16} className="text-red-400" />
                  <p className="text-slate-200 font-semibold text-sm">Portfolio Health Check</p>
                </div>
                <p className="text-muted text-xs">Get a full AI-powered diagnosis of concentration, tax efficiency, risk quality, and more.</p>
                <Link
                  to="/doctor"
                  className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl font-semibold text-sm text-white"
                  style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}
                >
                  <Stethoscope size={14} />
                  Open Portfolio Doctor →
                </Link>
              </div>
            )}
          </div>
        </>
      )}

      {/* ── Modals ── */}
      {showAdd && (
        <AddHoldingModal
          onClose={() => setShowAdd(false)}
          onAdd={addHolding}
          searchStocks={searchStocks}
        />
      )}

      {sellTarget && (
        <SellModal
          holding={sellTarget}
          onClose={() => setSellTarget(null)}
          onSell={sellHolding}
        />
      )}
    </div>
  )
}
