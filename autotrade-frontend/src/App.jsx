import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { useState, useEffect, useCallback } from 'react';
import { Toaster } from 'react-hot-toast';
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
import FundDetail         from './pages/FundDetail';
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
              <Route path="/s/:symbol"    element={<StockDetail />} />
              <Route path="/mf/:scheme"   element={<FundDetail />} />
            </Routes>
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
