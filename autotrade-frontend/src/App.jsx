import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { useState, useEffect, useCallback, Component } from 'react';
import { Toaster } from 'react-hot-toast';

// Catches React rendering errors so the user sees a message instead of a blank page.
class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, stack: null, componentStack: null };
  }
  static getDerivedStateFromError(err) { return { error: err }; }
  componentDidCatch(err, info) {
    console.error('[ErrorBoundary]', err, info);
    this.setState({ stack: err.stack, componentStack: info.componentStack });
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, background: '#080D1A', minHeight: '100vh', color: '#94A3B8', fontFamily: 'monospace' }}>
          <div style={{ maxWidth: 720 }}>
            <div style={{ color: '#EF4444', fontSize: 16, fontWeight: 700, marginBottom: 8 }}>Page render error</div>
            <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', color: '#CBD5E1', marginBottom: 8 }}>
              {this.state.error?.message}
            </pre>
            {this.state.stack && (
              <pre style={{ fontSize: 10, whiteSpace: 'pre-wrap', color: '#64748B', marginBottom: 8, maxHeight: 200, overflow: 'auto' }}>
                {this.state.stack}
              </pre>
            )}
            {this.state.componentStack && (
              <pre style={{ fontSize: 10, whiteSpace: 'pre-wrap', color: '#475569', marginBottom: 16, maxHeight: 120, overflow: 'auto' }}>
                {this.state.componentStack}
              </pre>
            )}
            <button
              onClick={() => { this.setState({ error: null, stack: null, componentStack: null }); window.history.back(); }}
              style={{ background: '#1E293B', border: '1px solid #334155', borderRadius: 8, padding: '8px 16px', color: '#F1F5F9', cursor: 'pointer' }}
            >
              ← Go back
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
import Sidebar      from './components/Sidebar';
import Navbar       from './components/Navbar';
import MobileNav    from './components/MobileNav';
import GlobalSearch from './components/GlobalSearch';
import Dashboard        from './pages/Dashboard';
import Trades           from './pages/Trades';
import Analytics        from './pages/Analytics';
import News             from './pages/News';
import Simulation       from './pages/Simulation';
import Settings         from './pages/Settings';
import Documentation    from './pages/Documentation';
import IndiaMarket      from './pages/IndiaMarket';
import IndiaSignals     from './pages/IndiaSignals';
import MutualFunds      from './pages/MutualFunds';
import IndiaFundamentals from './pages/IndiaFundamentals';
import Backtest          from './pages/Backtest';
import Portfolio         from './pages/Portfolio';
import Zerodha          from './pages/Zerodha';
import LiveMarket       from './pages/LiveMarket';
import Watchlist        from './pages/Watchlist';
import Chart           from './pages/Chart';
import MarketBreadth   from './pages/MarketBreadth';
import SectorHeatmap   from './pages/SectorHeatmap';
import MarketCalendar    from './pages/MarketCalendar';
import PortfolioTracker  from './pages/PortfolioTracker';
import SIPTracker        from './pages/SIPTracker';
import TaxCalculator     from './pages/TaxCalculator';
import AssetAllocation   from './pages/AssetAllocation';
import IPOTracker        from './pages/IPOTracker';
import StockChat          from './pages/StockChat';
import PortfolioDoctor    from './pages/PortfolioDoctor';
import EarningsAnalyzer  from './pages/EarningsAnalyzer';
import TradingAgent      from './pages/TradingAgent';
import IntelligenceDashboard from './pages/IntelligenceDashboard';
import AgentLog          from './pages/AgentLog';
import MarketScanner     from './pages/MarketScanner';
import StockDetail        from './pages/StockDetail';
import FundDetail            from './pages/FundDetail';
import PortfolioAnalytics   from './pages/PortfolioAnalytics';
import FloatingChatButton from './components/chat/FloatingChatButton';

export default function App() {
  const [searchOpen, setSearchOpen] = useState(false);

  const openSearch  = useCallback(() => setSearchOpen(true),  []);
  const closeSearch = useCallback(() => setSearchOpen(false), []);

  // Global ⌘K / Ctrl+K shortcut
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setSearchOpen(prev => !prev);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  return (
    <BrowserRouter>
      <div className="flex h-screen bg-surface text-slate-100 overflow-hidden">
        <Sidebar />
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <Navbar onSearchOpen={openSearch} />
          <main className="flex-1 overflow-y-auto p-6 pb-20 md:pb-6">
            <ErrorBoundary>
            <Routes>
              <Route path="/"              element={<Dashboard />}         />
              <Route path="/trades"        element={<Trades />}            />
              <Route path="/analytics"     element={<Analytics />}         />
              <Route path="/news"          element={<News />}              />
              <Route path="/simulation"    element={<Simulation />}        />
              <Route path="/settings"      element={<Settings />}          />
              <Route path="/documentation" element={<Documentation />}     />
              <Route path="/india"         element={<IndiaMarket />}       />
              <Route path="/india/signals" element={<IndiaSignals />}      />
              <Route path="/mutual-funds"  element={<MutualFunds />}       />
              <Route path="/fundamentals"  element={<IndiaFundamentals />} />
              <Route path="/backtest"      element={<Backtest />}          />
              <Route path="/portfolio"    element={<Portfolio />}         />
              {/* /zerodha now points at the unified portfolio (manual + MFs +
                  Sync-from-Kite). The legacy Kite Connect mirror page (OAuth,
                  GTT, MF orders, advanced features) is reachable at
                  /zerodha/connect. */}
              <Route path="/zerodha"          element={<PortfolioTracker />}  />
              <Route path="/zerodha/connect"  element={<Zerodha />}           />
              <Route path="/live-market"  element={<LiveMarket />}        />
              <Route path="/watchlist"       element={<Watchlist />}         />
              <Route path="/chart"          element={<Chart />}             />
              <Route path="/market-breadth"  element={<MarketBreadth />}   />
              <Route path="/sector-heatmap" element={<SectorHeatmap />}   />
              <Route path="/calendar"           element={<MarketCalendar />}    />
              <Route path="/portfolio-tracker" element={<PortfolioTracker />}  />
              <Route path="/sip"             element={<SIPTracker />}         />
              <Route path="/tax"             element={<TaxCalculator />}      />
              <Route path="/allocation"     element={<AssetAllocation />}    />
              <Route path="/ipo"            element={<IPOTracker />}         />
              <Route path="/chat"           element={<StockChat />}          />
              <Route path="/doctor"        element={<PortfolioDoctor />}    />
              <Route path="/earnings"      element={<EarningsAnalyzer />}   />
              <Route path="/agent"         element={<TradingAgent />}       />
              <Route path="/agent-log"         element={<AgentLog />}       />
              <Route path="/discover/scanner" element={<MarketScanner />}  />
              <Route path="/intelligence"  element={<IntelligenceDashboard />} />
              {/* Phase 2 — unified stock + fund detail pages */}
              <Route path="/portfolio-analytics" element={<PortfolioAnalytics />} />
              <Route path="/s/:symbol"    element={<StockDetail />} />
              <Route path="/mf/:scheme"   element={<FundDetail />} />
            </Routes>
            </ErrorBoundary>
          </main>
        </div>
      </div>
      {/* GlobalSearch lives outside main so backdrop covers the sidebar too */}
      <GlobalSearch open={searchOpen} onClose={closeSearch} />
      <MobileNav onSearchOpen={openSearch} />
      <FloatingChatButton />
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: { background: '#1E293B', color: '#F1F5F9', border: '1px solid #334155' },
        }}
      />
    </BrowserRouter>
  );
}
