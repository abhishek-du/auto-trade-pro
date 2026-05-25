import { useState } from 'react'
import { Briefcase, Plus, RefreshCw, List, PieChart as PieIcon, Receipt, BarChart2 } from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '../components/LoadingSpinner'
import { usePortfolioTracker } from '../hooks/usePortfolioTracker'
import PortfolioSelector from '../components/portfolio/PortfolioSelector'
import SummaryCards from '../components/portfolio/SummaryCards'
import HoldingsTable from '../components/portfolio/HoldingsTable'
import AddHoldingModal from '../components/portfolio/AddHoldingModal'
import SellModal from '../components/portfolio/SellModal'
import AllocationCharts from '../components/portfolio/AllocationCharts'
import TaxSummaryPanel from '../components/portfolio/TaxSummaryPanel'
import TransactionsTab from '../components/portfolio/TransactionsTab'

const TABS = [
  { id: 'holdings',     label: 'Holdings',     icon: List       },
  { id: 'allocation',   label: 'Allocation',   icon: PieIcon    },
  { id: 'tax',          label: 'Tax Summary',  icon: Receipt    },
  { id: 'transactions', label: 'Trade History', icon: BarChart2  },
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
              detail?.tax
                ? <TaxSummaryPanel tax={detail.tax} />
                : <p className="text-muted text-sm text-center py-8">No realised gains/losses yet.</p>
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
