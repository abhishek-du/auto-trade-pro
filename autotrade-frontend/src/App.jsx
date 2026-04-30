import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import Sidebar from './components/Sidebar';
import Navbar  from './components/Navbar';
import Dashboard  from './pages/Dashboard';
import Trades     from './pages/Trades';
import Analytics  from './pages/Analytics';
import News       from './pages/News';
import Simulation from './pages/Simulation';
import Settings   from './pages/Settings';

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen bg-surface text-slate-100 overflow-hidden">
        <Sidebar />
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <Navbar />
          <main className="flex-1 overflow-y-auto p-6">
            <Routes>
              <Route path="/"           element={<Dashboard />}  />
              <Route path="/trades"     element={<Trades />}     />
              <Route path="/analytics"  element={<Analytics />}  />
              <Route path="/news"       element={<News />}       />
              <Route path="/simulation" element={<Simulation />} />
              <Route path="/settings"   element={<Settings />}   />
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
