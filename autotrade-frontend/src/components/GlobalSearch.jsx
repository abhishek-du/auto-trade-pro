/**
 * GlobalSearch — ⌘K command palette
 *
 * Searches the full NSE equity universe (kite_instruments, ~9 600 EQ symbols)
 * and mutual funds simultaneously.  Keyboards: ↑↓ navigate, ↵ open,
 * ⌘+↵ AI analysis, Esc close, ⌘K toggle from anywhere.
 *
 * Recent searches are stored in localStorage under "atp_recent_searches".
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Clock, TrendingUp, BookOpen, Newspaper, Zap, X } from 'lucide-react';
import { apiFetch } from '../api/client';

// ── localStorage helpers ──────────────────────────────────────────────────────

const RECENT_KEY = 'atp_recent_searches';
const MAX_RECENT  = 5;

function loadRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; }
}

function saveRecent(item) {
  const prev = loadRecent().filter(r => r.symbol !== item.symbol);
  localStorage.setItem(RECENT_KEY, JSON.stringify([item, ...prev].slice(0, MAX_RECENT)));
}

function clearRecent() {
  localStorage.removeItem(RECENT_KEY);
}

// ── Signal chip ───────────────────────────────────────────────────────────────

function SignalChip({ signal }) {
  if (!signal) return null;
  const s = String(signal).toUpperCase();
  const cls =
    s === 'BUY'  || s === 'STRONG_BUY'  ? 'text-emerald-400 bg-emerald-500/15 border-emerald-500/30' :
    s === 'SELL' || s === 'STRONG_SELL' ? 'text-red-400 bg-red-500/15 border-red-500/30' :
                                           'text-slate-400 bg-slate-500/15 border-slate-500/30';
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border shrink-0 ${cls}`}>
      {s === 'STRONG_BUY' ? 'BUY' : s === 'STRONG_SELL' ? 'SELL' : s}
    </span>
  );
}

// ── Change badge ──────────────────────────────────────────────────────────────

function ChangeBadge({ change_pct }) {
  if (change_pct == null) return null;
  const pos = change_pct >= 0;
  return (
    <span className={`font-mono text-xs tabular-nums ${pos ? 'text-profit' : 'text-loss'}`}>
      {pos ? '+' : ''}{Number(change_pct).toFixed(2)}%
    </span>
  );
}

// ── Result row ────────────────────────────────────────────────────────────────

function ResultRow({ item, active, onSelect }) {
  const ref = useRef(null);
  useEffect(() => {
    if (active && ref.current) ref.current.scrollIntoView({ block: 'nearest' });
  }, [active]);

  return (
    <button
      ref={ref}
      className={[
        'w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors',
        active ? 'bg-white/5' : 'hover:bg-white/[0.03]',
      ].join(' ')}
      onMouseDown={e => { e.preventDefault(); onSelect(item); }}
    >
      {/* icon / avatar */}
      <div className={[
        'w-8 h-8 rounded-lg grid place-items-center shrink-0 text-xs font-bold',
        item.type === 'mf'
          ? 'bg-violet-500/20 text-violet-300'
          : 'bg-cyan/10 text-cyan',
      ].join(' ')}>
        {item.type === 'mf' ? 'MF' : (item.ticker || item.symbol || '?')[0]}
      </div>

      {/* name + meta */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-slate-100 text-sm font-semibold font-mono truncate">
            {item.ticker || item.scheme_code}
          </span>
          {item.exchange && <span className="text-[10px] text-muted bg-white/5 px-1.5 py-0.5 rounded shrink-0">{item.exchange}</span>}
          {item.foEnabled && <span className="text-[10px] text-muted bg-white/5 px-1.5 py-0.5 rounded shrink-0">F&O</span>}
          <SignalChip signal={item.signal} />
        </div>
        <div className="text-muted text-xs truncate mt-0.5">
          {item.name}
          {item.sector && item.sector !== 'Other' ? ` · ${item.sector}` : ''}
          {item.category ? ` · ${item.category}` : ''}
        </div>
      </div>

      {/* price / NAV */}
      <div className="text-right shrink-0 min-w-[70px]">
        {item.price != null && (
          <div className="text-slate-200 text-sm font-mono">
            ₹{Number(item.price).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
        )}
        {item.nav != null && (
          <div className="text-slate-200 text-sm font-mono">₹{Number(item.nav).toFixed(2)}</div>
        )}
        <ChangeBadge change_pct={item.change_pct} />
      </div>
    </button>
  );
}

// ── Section header ────────────────────────────────────────────────────────────

function SectionHeader({ label, count }) {
  return (
    <div className="flex items-center justify-between px-3 pt-3 pb-1">
      <span className="text-[10px] text-muted font-semibold uppercase tracking-widest">{label}</span>
      {count != null && <span className="text-[10px] text-muted">{count} match{count !== 1 ? 'es' : ''}</span>}
    </div>
  );
}

// ── Quick action row ──────────────────────────────────────────────────────────

function ActionRow({ icon: Icon, label, kbd, active, onMouseDown }) {
  return (
    <button
      className={[
        'w-full flex items-center gap-3 px-3 py-2 text-sm text-slate-300 transition-colors',
        active ? 'bg-white/5' : 'hover:bg-white/[0.03]',
      ].join(' ')}
      onMouseDown={onMouseDown}
    >
      <Icon size={14} className="text-muted shrink-0" />
      <span className="flex-1 text-left">{label}</span>
      {kbd && <span className="text-[10px] font-mono bg-white/5 border border-white/10 px-1.5 py-0.5 rounded text-muted">{kbd}</span>}
    </button>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function GlobalSearch({ open, onClose }) {
  const navigate   = useNavigate();
  const inputRef   = useRef(null);
  const [query, setQuery] = useState('');
  const [stocks, setStocks] = useState([]);
  const [funds,  setFunds]  = useState([]);
  const [recent, setRecent] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);

  // Live prices cache for enriching stock results
  const priceCache = useRef({});

  // ── Reset state on open ───────────────────────────────────────────────────

  useEffect(() => {
    if (open) {
      setQuery('');
      setStocks([]);
      setFunds([]);
      setActiveIdx(0);
      setRecent(loadRecent());
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // ── Search debounce ───────────────────────────────────────────────────────

  const searchTimer = useRef(null);

  const doSearch = useCallback(async (q) => {
    if (!q || q.trim().length < 1) { setStocks([]); setFunds([]); setLoading(false); return; }
    setLoading(true);
    try {
      const [stockRes, mfRes] = await Promise.allSettled([
        apiFetch(`/api/v1/portfolios/search/stocks?q=${encodeURIComponent(q)}`),
        apiFetch(`/api/v1/portfolios/search/mf?q=${encodeURIComponent(q)}`),
      ]);

      const rawStocks = stockRes.status === 'fulfilled' ? (stockRes.value || []) : [];
      const rawFunds  = mfRes.status  === 'fulfilled' ? (mfRes.value  || []) : [];

      // Enrich stocks with live prices from cache or a quick live-prices call
      const tickers = rawStocks.map(s => s.ticker).filter(Boolean);
      if (tickers.length > 0) {
        try {
          const livePrices = await apiFetch(`/api/v1/india/live-prices`);
          if (livePrices && typeof livePrices === 'object') {
            Object.assign(priceCache.current, livePrices);
          }
        } catch { /* price enrichment is best-effort */ }
      }

      const enrichedStocks = rawStocks.slice(0, 10).map(s => {
        const cached =
          priceCache.current[s.ticker + '.NS'] ||
          priceCache.current[s.symbol] ||
          priceCache.current[s.ticker] || {};
        return {
          ...s,
          type: 'stock',
          exchange: 'NSE',
          price:      cached.price ?? null,
          change_pct: cached.change_pct ?? null,
          signal:     cached.signal ?? null,
        };
      });

      const enrichedFunds = rawFunds.slice(0, 6).map(f => ({
        type: 'mf',
        ticker: f.scheme_code,
        scheme_code: f.scheme_code,
        symbol: `MF:${f.scheme_code}`,
        name: f.scheme_name,
        category: f.category,
        nav: f.nav ?? null,
        change_pct: null,
        signal: null,
      }));

      setStocks(enrichedStocks);
      setFunds(enrichedFunds);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    clearTimeout(searchTimer.current);
    if (!open) return;
    if (!query.trim()) { setStocks([]); setFunds([]); setLoading(false); return; }
    setLoading(true);
    searchTimer.current = setTimeout(() => doSearch(query), 200);
    return () => clearTimeout(searchTimer.current);
  }, [query, open, doSearch]);

  // ── Flat list for keyboard nav ────────────────────────────────────────────

  const showing = query.trim() ? [...stocks, ...funds] : recent;

  useEffect(() => { setActiveIdx(0); }, [query]);

  // ── Navigation ────────────────────────────────────────────────────────────

  const openItem = useCallback((item) => {
    if (!item) return;
    if (item.type === 'recent') {
      navigate(item.path);
    } else if (item.type === 'mf') {
      const path = `/mf/${item.scheme_code}`;
      saveRecent({ ...item, type: 'recent', path });
      navigate(path);
    } else {
      const ticker = item.ticker || item.symbol?.replace('.NS', '');
      const path = `/s/${ticker}`;
      saveRecent({ ...item, type: 'recent', path });
      navigate(path);
    }
    onClose();
  }, [navigate, onClose]);

  const handleKey = useCallback((e) => {
    if (e.key === 'Escape') { onClose(); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx(i => Math.min(i + 1, showing.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) {
        // ⌘+↵ → AI analysis (navigate to stock page scrolled to analysis)
        const item = showing[activeIdx];
        if (item) openItem(item);
      } else {
        openItem(showing[activeIdx]);
      }
    }
  }, [showing, activeIdx, openItem, onClose]);

  if (!open) return null;

  const hasQuery = query.trim().length > 0;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[9998] bg-black/60 backdrop-blur-sm"
        onMouseDown={onClose}
      />

      {/* Palette */}
      <div className="fixed inset-0 z-[9999] flex items-start justify-center pt-[12vh] px-4 pointer-events-none">
        <div
          className="w-full max-w-2xl rounded-2xl overflow-hidden pointer-events-auto"
          style={{
            background: 'linear-gradient(145deg, #131E30, #0F1829)',
            border: '1px solid rgba(59,130,246,0.25)',
            boxShadow: '0 32px 80px -10px rgba(0,0,0,0.7), 0 0 0 1px rgba(59,130,246,0.12)',
          }}
          onKeyDown={handleKey}
        >
          {/* Input row */}
          <div className="flex items-center gap-3 px-4 h-14 border-b border-border">
            {loading
              ? <div className="w-4 h-4 border-2 border-accent/40 border-t-accent rounded-full animate-spin shrink-0" />
              : <Search size={16} className="text-muted shrink-0" />
            }
            <input
              ref={inputRef}
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search any stock, MF, sector…"
              className="flex-1 bg-transparent outline-none text-slate-100 text-base placeholder:text-muted"
            />
            {query && (
              <button className="text-muted hover:text-slate-300" onMouseDown={() => setQuery('')}>
                <X size={14} />
              </button>
            )}
            <kbd className="hidden sm:flex text-[10px] font-mono bg-white/5 border border-white/10 px-1.5 py-0.5 rounded text-muted">ESC</kbd>
          </div>

          {/* Results */}
          <div className="max-h-[60vh] overflow-y-auto">

            {/* Recent (no query) */}
            {!hasQuery && recent.length > 0 && (
              <>
                <div className="flex items-center justify-between px-3 pt-3 pb-1">
                  <span className="text-[10px] text-muted font-semibold uppercase tracking-widest">Recent</span>
                  <button
                    className="text-[10px] text-muted hover:text-slate-300"
                    onMouseDown={() => { clearRecent(); setRecent([]); }}
                  >
                    Clear
                  </button>
                </div>
                {recent.map((item, i) => (
                  <ResultRow key={item.symbol || i} item={item} active={activeIdx === i} onSelect={openItem} />
                ))}
              </>
            )}

            {/* Stocks */}
            {hasQuery && stocks.length > 0 && (
              <>
                <SectionHeader label="Stocks" count={stocks.length} />
                {stocks.map((s, i) => (
                  <ResultRow key={s.symbol} item={s} active={activeIdx === i} onSelect={openItem} />
                ))}
              </>
            )}

            {/* Mutual Funds */}
            {hasQuery && funds.length > 0 && (
              <>
                <SectionHeader label="Mutual Funds" count={funds.length} />
                {funds.map((f, i) => (
                  <ResultRow
                    key={f.scheme_code}
                    item={f}
                    active={activeIdx === stocks.length + i}
                    onSelect={openItem}
                  />
                ))}
              </>
            )}

            {/* Empty state */}
            {hasQuery && !loading && stocks.length === 0 && funds.length === 0 && (
              <div className="px-4 py-8 text-center text-muted text-sm">
                No results for <span className="text-slate-300 font-mono">"{query}"</span>
              </div>
            )}

            {/* Quick actions (always shown when something is selected) */}
            {hasQuery && stocks.length > 0 && (
              <>
                <div className="border-t border-border mt-1" />
                <SectionHeader label="Quick actions" />
                <ActionRow
                  icon={Zap}
                  label={<>Run AI analysis on <span className="font-mono text-slate-200">{stocks[0]?.ticker}</span></>}
                  kbd="⌘ ↵"
                  active={false}
                  onMouseDown={() => { openItem(stocks[0]); }}
                />
                <ActionRow
                  icon={TrendingUp}
                  label={<>Add <span className="font-mono text-slate-200">{stocks[0]?.ticker}</span> to watchlist</>}
                  active={false}
                  onMouseDown={() => {
                    const ticker = stocks[0]?.ticker;
                    if (ticker) navigate(`/watchlist?add=${ticker}`);
                    onClose();
                  }}
                />
              </>
            )}
          </div>

          {/* Footer */}
          <div className="border-t border-border px-4 py-2 flex items-center gap-4 text-[10px] text-muted">
            <span className="flex items-center gap-1">
              <kbd className="font-mono bg-white/5 border border-white/10 px-1 rounded">↑</kbd>
              <kbd className="font-mono bg-white/5 border border-white/10 px-1 rounded">↓</kbd>
              Navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="font-mono bg-white/5 border border-white/10 px-1 rounded">↵</kbd>
              Open
            </span>
            <span className="flex items-center gap-1">
              <kbd className="font-mono bg-white/5 border border-white/10 px-1 rounded">⌘↵</kbd>
              AI analysis
            </span>
            <span className="flex items-center gap-1">
              <kbd className="font-mono bg-white/5 border border-white/10 px-1 rounded">⌘K</kbd>
              Toggle
            </span>
            <span className="ml-auto flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-profit shrink-0" />
              ~9,600 stocks · all MFs
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
