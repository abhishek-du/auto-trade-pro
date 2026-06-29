import { useState, useEffect, useCallback } from 'react';
import { Save, Plus, X, Settings as SettingsIcon, AlertTriangle, TrendingUp, Shield } from 'lucide-react';
import toast from 'react-hot-toast';
import LoadingSpinner from '../components/LoadingSpinner';
import { getSettings, saveSettings, apiFetch } from '../api/client';

const DEFAULT_CFG = {
  max_open_positions:         5,
  min_cash_buffer:            10,   // displayed as %, stored as fraction
  agent_default_product:      'CNC',
  agent_confidence_threshold: 30,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function FieldRow({ label, hint, children }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-4 py-4 border-b border-border last:border-0">
      <div className="sm:w-64 shrink-0">
        <p className="text-slate-300 text-sm font-medium">{label}</p>
        {hint && <p className="text-muted text-xs mt-1 leading-snug">{hint}</p>}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  );
}

function NumberInput({ value, onChange, min, max, step = 1, suffix }) {
  return (
    <div className="flex items-center gap-2 max-w-40">
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        step={step}
        className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 tabular-nums focus:outline-none focus:border-accent"
      />
      {suffix && <span className="text-muted text-sm shrink-0">{suffix}</span>}
    </div>
  );
}

// ── Product type selector (CNC vs MIS) ───────────────────────────────────────

function ProductSelector({ value, onChange }) {
  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        {[
          {
            id: 'CNC',
            label: 'CNC — Delivery',
            sub: 'Long-only · T+1 settlement · Hold overnight',
            color: 'cyan',
          },
          {
            id: 'MIS',
            label: 'MIS — Intraday',
            sub: 'Short selling allowed · Must close by 3:20 PM IST',
            color: 'amber',
          },
        ].map(opt => (
          <button
            key={opt.id}
            onClick={() => onChange(opt.id)}
            className={`flex-1 text-left px-4 py-3 rounded-xl border transition-all ${
              value === opt.id
                ? opt.id === 'CNC'
                  ? 'bg-cyan/10 border-cyan/50 text-cyan'
                  : 'bg-amber-500/10 border-amber-500/50 text-amber-400'
                : 'bg-surface border-border text-muted hover:border-border/80'
            }`}
          >
            <p className="font-semibold text-sm">{opt.label}</p>
            <p className="text-[11px] mt-0.5 opacity-75">{opt.sub}</p>
          </button>
        ))}
      </div>
      {value === 'MIS' && (
        <div className="flex items-start gap-2 bg-amber-500/10 border border-amber-500/25 rounded-lg px-3 py-2.5">
          <AlertTriangle size={13} className="text-amber-400 mt-0.5 shrink-0" />
          <p className="text-amber-300 text-[11px] leading-relaxed">
            <strong>NSE/BSE Rule:</strong> MIS positions are auto-squared off by Zerodha at 3:20 PM IST using market orders.
            Prajna closes them at 3:15 PM with limit orders for better fills.
            Short selling (SELL without holding shares) is only permitted in MIS — SEBI prohibits delivery shorts.
          </p>
        </div>
      )}
      {value === 'CNC' && (
        <p className="text-muted text-[11px]">
          CNC is long-only. Short sell signals (MEAN_REVERSION_SHORT strategy) automatically
          use MIS regardless of this setting — SEBI/NSE rule.
        </p>
      )}
    </div>
  );
}

// ── NSE Watchlist editor (live add / remove via API) ─────────────────────────

function NseWatchlistEditor() {
  const [symbols,  setSymbols]  = useState([]);
  const [input,    setInput]    = useState('');
  const [busy,     setBusy]     = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await apiFetch('/api/v1/india/user-watchlist');
      // Strip .NS suffix for display
      setSymbols((d.symbols || []).map(s => s.replace('.NS', '')));
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  const add = async () => {
    const sym = input.trim().toUpperCase().replace('.NS', '').replace('.BO', '');
    if (!sym || symbols.includes(sym)) return;
    setBusy(true);
    try {
      await apiFetch(`/api/v1/india/user-watchlist/${sym}`, { method: 'POST' });
      setSymbols(s => [...s, sym]);
      setInput('');
      toast.success(`${sym} added to watchlist`);
    } catch {
      toast.error('Could not add symbol');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (sym) => {
    setBusy(true);
    try {
      await apiFetch(`/api/v1/india/user-watchlist/${sym}`, { method: 'DELETE' });
      setSymbols(s => s.filter(x => x !== sym));
      toast.success(`${sym} removed`);
    } catch {
      toast.error('Could not remove symbol');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {symbols.length === 0 ? (
        <p className="text-muted text-xs italic">No custom symbols yet — the agent already scans the full NSE universe via the market scanner.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {symbols.map(sym => (
            <span
              key={sym}
              className="inline-flex items-center gap-1.5 bg-cyan/10 border border-cyan/30 text-cyan text-xs font-mono px-2.5 py-1 rounded-lg"
            >
              {sym}
              <button
                onClick={() => remove(sym)}
                disabled={busy}
                className="hover:text-red-400 transition-colors disabled:opacity-40"
                aria-label={`Remove ${sym}`}
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="e.g. JAYBARMARU"
          value={input}
          onChange={e => setInput(e.target.value.toUpperCase())}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          className="bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-muted focus:outline-none focus:border-cyan w-48 font-mono"
        />
        <button
          onClick={add}
          disabled={busy || !input.trim()}
          className="flex items-center gap-1.5 px-3 py-2 bg-cyan/10 hover:bg-cyan/20 border border-cyan/30 text-cyan rounded-lg text-sm transition-colors disabled:opacity-40"
        >
          <Plus size={14} />
          Add
        </button>
      </div>
      <p className="text-muted text-[10px]">
        These symbols get priority in the agent's scan universe. The agent already covers all 9,600+ NSE EQ symbols automatically — add here to ensure a specific stock is never missed.
      </p>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Settings() {
  const [cfg,       setCfg]      = useState(DEFAULT_CFG);
  const [openRisk,  setOpenRisk] = useState(null);   // current portfolio risk % live
  const [loading,   setLoading]  = useState(true);
  const [saving,    setSaving]   = useState(false);

  useEffect(() => {
    Promise.all([
      getSettings().catch(() => ({})),
      apiFetch('/api/v1/agent/status').catch(() => null),
    ]).then(([d, status]) => {
      setCfg({
        ...DEFAULT_CFG,
        max_open_positions:         d.max_open_positions ?? DEFAULT_CFG.max_open_positions,
        min_cash_buffer:            d.min_cash_buffer != null ? Math.round(d.min_cash_buffer * 100) : DEFAULT_CFG.min_cash_buffer,
        agent_default_product:      d.agent_default_product ?? DEFAULT_CFG.agent_default_product,
        agent_confidence_threshold: d.agent_confidence_threshold ?? DEFAULT_CFG.agent_confidence_threshold,
      });
      // open_risk_pct from agent status (already in %)
      if (status?.portfolio?.open_risk_pct != null) {
        setOpenRisk(Number(status.portfolio.open_risk_pct));
      }
    }).finally(() => setLoading(false));
  }, []);

  const set = (key) => (val) => setCfg(c => ({ ...c, [key]: val }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await saveSettings({
        max_open_positions:         cfg.max_open_positions,
        min_cash_buffer:            cfg.min_cash_buffer / 100,
        agent_default_product:      cfg.agent_default_product,
        agent_confidence_threshold: cfg.agent_confidence_threshold,
      });
      toast.success('Settings saved — takes effect on next cycle');
    } catch {
      toast.error('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <LoadingSpinner message="Loading settings…" />;

  return (
    <div className="space-y-6 max-w-2xl">

      {/* Banner */}
      <div className="flex items-start gap-3 bg-blue-500/10 border border-blue-500/25 rounded-xl p-4">
        <AlertTriangle size={16} className="text-blue-400 mt-0.5 shrink-0" />
        <p className="text-blue-300 text-xs leading-relaxed">
          <strong>Paper trading simulation only.</strong> No real money is at risk.
          Changes take effect on the next agent cycle (within 60 s).
        </p>
      </div>

      {/* Risk & Position Controls */}
      <div className="glass-panel border border-border rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
          <TrendingUp size={16} className="text-cyan" />
          <h2 className="text-slate-200 font-semibold text-sm">Risk &amp; Position Controls</h2>
        </div>
        <div className="px-5 divide-y divide-border/50">

          <FieldRow
            label="Max Open Positions"
            hint="Hard ceiling on simultaneous open trades."
          >
            <div className="space-y-2">
              <NumberInput value={cfg.max_open_positions} onChange={set('max_open_positions')} min={1} max={30} step={1} suffix="positions" />
              {openRisk != null && (
                <p className="text-muted text-[11px]">
                  Current stop-loss risk deployed: <span className="text-slate-300 font-mono">{openRisk.toFixed(1)}%</span> of equity
                  <span className="text-green-500/70 ml-1">(no cap in paper mode)</span>
                </p>
              )}
            </div>
          </FieldRow>

          <FieldRow
            label="Min Cash Buffer"
            hint="Always keep this fraction of equity as dry cash. Only enforced in live trading."
          >
            <NumberInput value={cfg.min_cash_buffer} onChange={set('min_cash_buffer')} min={0} max={50} step={1} suffix="%" />
          </FieldRow>

          <FieldRow
            label="Min Signal Confidence"
            hint="Agent skips any trade below this threshold. Lower = more trades, lower quality."
          >
            <div className="space-y-2">
              <NumberInput value={cfg.agent_confidence_threshold} onChange={set('agent_confidence_threshold')} min={0} max={100} step={5} suffix="%" />
              <p className="text-muted text-[11px]">
                {cfg.agent_confidence_threshold < 40
                  ? <span className="text-amber-400">Low threshold — agent will trade weak signals too.</span>
                  : cfg.agent_confidence_threshold >= 70
                  ? <span className="text-green-500/80">High threshold — only strong setups.</span>
                  : <span className="text-slate-400">Balanced — good for paper trading.</span>}
              </p>
            </div>
          </FieldRow>

        </div>
      </div>

      {/* Trading Product */}
      <div className="glass-panel border border-border rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
          <Shield size={16} className="text-green-400" />
          <h2 className="text-slate-200 font-semibold text-sm">Trading Product</h2>
          <span className="text-muted text-xs">— NSE/BSE segment for new trades</span>
        </div>
        <div className="px-5 py-4 space-y-2">
          <ProductSelector value={cfg.agent_default_product} onChange={set('agent_default_product')} />
          <div className="mt-3 grid grid-cols-3 gap-2 text-[10px] text-muted">
            <div className="bg-surface/50 rounded-lg px-3 py-2">
              <p className="font-semibold text-slate-400 mb-1">CNC Delivery</p>
              <p>Buy stocks with full cash. Hold days/months. Long only. T+1 settlement.</p>
            </div>
            <div className="bg-surface/50 rounded-lg px-3 py-2">
              <p className="font-semibold text-slate-400 mb-1">MIS Intraday</p>
              <p>Short sell allowed. Up to 5× leverage. Zerodha auto-squares at 3:20 PM.</p>
            </div>
            <div className="bg-surface/50 rounded-lg px-3 py-2">
              <p className="font-semibold text-slate-400 mb-1">NRML F&O</p>
              <p>Futures &amp; options. Overnight allowed. Not yet supported by agent.</p>
            </div>
          </div>
        </div>
      </div>

      {/* NSE Watchlist */}
      <div className="glass-panel border border-border rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
          <SettingsIcon size={16} className="text-accent" />
          <h2 className="text-slate-200 font-semibold text-sm">Priority Watchlist</h2>
          <span className="text-muted text-xs">— NSE stocks always included in agent's scan</span>
        </div>
        <div className="px-5 py-4">
          <NseWatchlistEditor />
        </div>
      </div>

      {/* Save */}
      <div className="flex justify-end">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 px-6 py-2.5 bg-accent hover:bg-accent/90 text-white rounded-lg text-sm font-semibold transition-colors disabled:opacity-50"
        >
          <Save size={16} className={saving ? 'animate-pulse' : ''} />
          {saving ? 'Saving…' : 'Save Settings'}
        </button>
      </div>

    </div>
  );
}
