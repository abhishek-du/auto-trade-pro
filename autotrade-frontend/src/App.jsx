import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import Sidebar    from './components/Sidebar';
import Navbar     from './components/Navbar';
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

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen bg-surface text-slate-100 overflow-hidden">
        <Sidebar />
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <Navbar />
          <main className="flex-1 overflow-y-auto p-6">
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
              <Route path="/zerodha"      element={<Zerodha />}           />
              <Route path="/live-market"  element={<LiveMarket />}        />
            </Routes>
          </main>
        </div>
      </div>
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: { background: '#1E293B', color: '#F1F5F9', border: '1px solid #334155' },
        }}
      />
    </BrowserRouter>
  );
}
