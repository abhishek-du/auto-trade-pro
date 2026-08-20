import { useState, useEffect, useCallback } from 'react';
import { Save, Plus, X, Settings as SettingsIcon, AlertTriangle, TrendingUp, Shield, Layers, Ban, Ghost, Zap } from 'lucide-react';
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


// ── Strategy Execution Toggles (BACKEND-WIRED) ─────────────────────────────
// Unlike StrategiesPanel below — which is a local display preference — these
// switches write to RuntimeConfig in the database. Every process (uvicorn,
// Celery worker, news engine) reads the flag at its next decision point, so a
// change takes effect without a restart.
//
// Flags default to ON when absent: a wiped table or a fresh DB must never
// silently halt trading. That means "no data" reads as enabled, which is why
// the panel shows an explicit error state rather than defaulting to off.

const EXECUTION_STRATEGIES = [
  { id: 'master_intelligence', name: 'Master Intelligence (Path A)',
    desc: 'Scores the NSE universe into the shortlist and runs two discretionary exits. Does NOT originate trades.' },
  { id: 'india_trade_loop', name: 'India Trade Loop (Path B — Technical)',
    desc: 'Technical entries from the shortlist. Gates entries only — exits and stop-losses keep running when off.' },
  { id: 'news_engine', name: 'News Engine (Path C — Event-Driven)',
    desc: 'LLM ReAct debate on canonical events. When off, news is still crawled and classified; only execution stops.' },
  { id: 'pre_event_gap', name: 'Pre-Event Gap (Path D)',
    desc: 'Nowcasts the surprise on scheduled corporate events 1–15 days out.' },
  { id: 'direct_news', name: 'Direct News (Path E)',
    desc: 'Trades straight off event classification, no LLM debate.' },
  { id: 'tactical', name: 'Tactical Pipeline (Path F)',
    desc: 'Intraday + mean-reversion scans. When off, signals are still scored and persisted; only execution stops.' },
];

function StrategyExecutionPanel() {
  const [flags,   setFlags]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [saving,  setSaving]  = useState(null);   // id currently being written

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiFetch('/api/v1/settings/strategies');
      setFlags(d?.flags ?? null);
      setError(null);
    } catch (e) {
      setError(e?.message || 'Could not load strategy toggles');
      setFlags(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = async (id) => {
    if (!flags || saving) return;
    const next = !flags[id];
    // Optimistic, then reconciled against the server's echo below — the server
    // is the authority on what is actually running.
    setFlags(f => ({ ...f, [id]: next }));
    setSaving(id);
    try {
      const res = await apiFetch('/api/v1/settings/strategies', {
        method: 'POST',
        body: JSON.stringify({ flags: { [id]: next } }),
      });
      if (res?.flags) setFlags(res.flags);
      const label = EXECUTION_STRATEGIES.find(s => s.id === id)?.name || id;
      toast.success(`${label} ${next ? 'enabled' : 'disabled'} — effective now`);
      if (res?.all_disabled) {
        toast('All strategies are off. Nothing will open a new trade.', { icon: '\u26a0\ufe0f' });
      }
    } catch (e) {
      setFlags(f => ({ ...f, [id]: !next }));   // roll back
      toast.error(e?.message || 'Could not save — the strategy was not changed');
    } finally {
      setSaving(null);
    }
  };

  const allOff = flags && EXECUTION_STRATEGIES.every(s => !flags[s.id]);

  return (
    <div className="glass-panel border border-border rounded-xl overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
        <Zap size={16} className="text-emerald-400" />
        <h2 className="text-slate-200 font-semibold text-sm">Strategy Execution</h2>
        <span className="text-muted text-xs">— live control, takes effect immediately</span>
      </div>

      <div className="flex items-start gap-2.5 mx-5 mt-4 bg-emerald-500/10 border border-emerald-500/25 rounded-lg px-3 py-2.5">
        <Zap size={13} className="text-emerald-400 mt-0.5 shrink-0" />
        <p className="text-emerald-300 text-[11px] leading-relaxed">
          These switches <strong>do control the live agent</strong>. Changes are stored in the database and
          picked up by every process at its next decision — no restart required.
          Stop-losses are never gated: open positions keep exiting even with every strategy off.
        </p>
      </div>

      {allOff && (
        <div className="flex items-start gap-2.5 mx-5 mt-3 bg-amber-500/10 border border-amber-500/25 rounded-lg px-3 py-2.5">
          <AlertTriangle size={13} className="text-amber-400 mt-0.5 shrink-0" />
          <p className="text-amber-300 text-[11px] leading-relaxed">
            Every strategy is off. Nothing will originate a new trade.
          </p>
        </div>
      )}

      <div className="px-5 py-2">
        {loading && <div className="py-6 flex justify-center"><LoadingSpinner /></div>}

        {!loading && error && (
          <div className="py-5 text-center">
            <p className="text-rose-300 text-xs mb-2">{error}</p>
            <button onClick={load}
              className="text-cyan text-xs underline hover:no-underline focus:outline-none focus:ring-2 focus:ring-cyan/50 rounded">
              Try again
            </button>
          </div>
        )}

        {!loading && !error && flags && (
          <div className="divide-y divide-border/50">
            {EXECUTION_STRATEGIES.map(s => (
              <div key={s.id} className="flex items-center gap-3 py-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-slate-200 text-sm font-medium">{s.name}</p>
                    <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${
                      flags[s.id] ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-600/30 text-slate-400'
                    }`}>
                      {flags[s.id] ? 'On' : 'Off'}
                    </span>
                  </div>
                  <p className="text-muted text-[11px] mt-0.5 leading-snug">{s.desc}</p>
                </div>
                <ToggleSwitch
                  checked={!!flags[s.id]}
                  disabled={saving === s.id}
                  onClick={() => toggle(s.id)}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Supporting components (UI-only — not wired to the backend) ──────────────
// The six ORIGINATION PATHS are controlled by StrategyExecutionPanel above,
// which reads its state from the API and is therefore always accurate. This
// list covers only the components that have no individual runtime switch.
//
// Deliberately does NOT repeat the six paths. It used to, and the copies drifted:
// on 2026-08-20 this panel still showed Path A and Path B as "BLOCKED
// (architecture)" a day after the block was lifted, listed four F&O strategies
// as LIVE months after the subsystem was deleted in 91457d7, and never listed
// Path F at all. Hardcoded status that duplicates backend state goes stale.
//
// Toggle state here lives entirely in the browser (localStorage) and changes
// nothing about what the agent does.

const STORAGE_KEY = 'prajna_strategy_toggles_v1';

const STRATEGIES = [
  {
    id: 'intraday_mis',
    name: 'Intraday MIS',
    desc: 'Same-day equity + index-option entries, scheduled 9:30am entry and 3:10pm square-off.',
    status: 'LIVE',
  },
  {
    id: 'shock_guard',
    name: 'Market Shock Guard',
    desc: 'Tightens or flattens open longs on a sudden index drop or high-severity news burst.',
    status: 'LIVE',
  },
  {
    id: 'ml_predictor',
    name: 'ML Direction Predictor',
    desc: 'Per-symbol LSTM 3-class (UP/DOWN/FLAT) prediction, ±15 nudge on the technical score.',
    status: 'LIVE',
  },
  {
    id: 'scan_paper_trader',
    name: 'SCAN Paper Trader',
    desc: 'A second, independent scanner-driven paper-trading loop referenced by a config flag with no code behind it yet.',
    status: 'OFF',
  },
];

const STATUS_META = {
  LIVE:    { label: 'LIVE',    dot: 'bg-emerald-400', text: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/25' },
  BLOCKED: { label: 'BLOCKED', dot: 'bg-amber-400',   text: 'text-amber-400',   bg: 'bg-amber-500/10',   border: 'border-amber-500/25' },
  OFF:     { label: 'OFF',     dot: 'bg-slate-500',   text: 'text-slate-400',   bg: 'bg-slate-500/10',   border: 'border-slate-500/25' },
};

function loadToggleState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const saved = raw ? JSON.parse(raw) : {};
    const state = {};
    for (const s of STRATEGIES) {
      state[s.id] = s.id in saved ? Boolean(saved[s.id]) : s.status === 'LIVE';
    }
    return state;
  } catch {
    const state = {};
    for (const s of STRATEGIES) state[s.id] = s.status === 'LIVE';
    return state;
  }
}

function StatusPill({ status }) {
  const m = STATUS_META[status];
  return (
    <span className={`inline-flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full ${m.bg} ${m.text} border ${m.border} shrink-0`}>
      <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
      {m.label}
    </span>
  );
}

function ToggleSwitch({ checked, onClick, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={onClick}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-cyan/50 disabled:opacity-40 disabled:cursor-not-allowed ${
        checked ? 'bg-emerald-500' : 'bg-slate-700'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

function ConfirmToggleDialog({ strategy, nextState, onConfirm, onCancel }) {
  if (!strategy) return null;
  const turningOn = nextState;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4" onClick={onCancel}>
      <div
        className="glass-panel border border-border rounded-xl max-w-md w-full p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <div className={`p-2 rounded-lg shrink-0 ${turningOn ? 'bg-emerald-500/15' : 'bg-red-500/15'}`}>
            <AlertTriangle size={18} className={turningOn ? 'text-emerald-400' : 'text-red-400'} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-slate-100 font-semibold text-sm">
              {turningOn ? 'Turn on' : 'Turn off'} "{strategy.name}"?
            </p>
            <p className="text-muted text-xs mt-1.5 leading-relaxed">{strategy.desc}</p>
            {strategy.status === 'LIVE' && (
              <p className="text-amber-400 text-[11px] mt-2 leading-relaxed">
                This strategy is currently <strong>live</strong>. This switch is a local UI preference only —
                it does not call the trading agent and does not change what it actually does.
              </p>
            )}
            {strategy.status === 'BLOCKED' && (
              <p className="text-amber-400 text-[11px] mt-2 leading-relaxed">
                Blocked by the News-Only architecture decision, not a settings flag — this switch cannot
                actually re-enable it.
              </p>
            )}
            {strategy.status === 'OFF' && (
              <p className="text-muted text-[11px] mt-2 leading-relaxed">
                No code currently reads this flag — this switch has no effect either way.
              </p>
            )}
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm font-medium text-muted hover:text-slate-200 hover:bg-white/5 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
              turningOn
                ? 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/30'
                : 'bg-red-500/15 hover:bg-red-500/25 text-red-400 border border-red-500/30'
            }`}
          >
            Confirm {turningOn ? 'On' : 'Off'}
          </button>
        </div>
      </div>
    </div>
  );
}

function StrategiesPanel() {
  const [toggles,  setToggles]  = useState(loadToggleState);
  const [pending,  setPending]  = useState(null); // strategy pending confirmation, or null

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toggles));
  }, [toggles]);

  const requestToggle = (strategy) => setPending(strategy);

  const confirmToggle = () => {
    if (!pending) return;
    const next = !toggles[pending.id];
    setToggles(t => ({ ...t, [pending.id]: next }));
    toast.success(`${pending.name} turned ${next ? 'on' : 'off'} (local preference only)`);
    setPending(null);
  };

  const groups = [
    { key: 'LIVE',    label: 'Live',                 items: STRATEGIES.filter(s => s.status === 'LIVE') },
    { key: 'BLOCKED', label: 'Blocked (architecture)', items: STRATEGIES.filter(s => s.status === 'BLOCKED') },
    { key: 'OFF',     label: 'Off (no backend flag)',  items: STRATEGIES.filter(s => s.status === 'OFF') },
  ];

  return (
    <div className="glass-panel border border-border rounded-xl overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
        <Layers size={16} className="text-purple-400" />
        <h2 className="text-slate-200 font-semibold text-sm">Supporting Components</h2>
        <span className="text-muted text-xs">— no individual runtime switch</span>
      </div>

      <div className="flex items-start gap-2.5 mx-5 mt-4 bg-blue-500/10 border border-blue-500/25 rounded-lg px-3 py-2.5">
        <AlertTriangle size={13} className="text-blue-400 mt-0.5 shrink-0" />
        <p className="text-blue-300 text-[11px] leading-relaxed">
          Components that run alongside the six origination paths. These switches are a
          <strong> local display preference only</strong> and do not affect trading.
          The six paths themselves are controlled in <strong>Strategy Execution</strong> above.
        </p>
      </div>

      <div className="px-5 py-2">
        {groups.map(group => group.items.length > 0 && (
          <div key={group.key} className="py-3">
            <p className="text-[10px] font-bold uppercase tracking-wider text-muted mb-2 flex items-center gap-1.5">
              {group.key === 'BLOCKED' && <Ban size={11} />}
              {group.key === 'OFF' && <Ghost size={11} />}
              {group.label}
            </p>
            <div className="divide-y divide-border/50">
              {group.items.map(s => (
                <div key={s.id} className="flex items-center gap-3 py-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="text-slate-200 text-sm font-medium">{s.name}</p>
                      <StatusPill status={s.status} />
                    </div>
                    <p className="text-muted text-[11px] mt-0.5 leading-snug">{s.desc}</p>
                  </div>
                  <ToggleSwitch checked={!!toggles[s.id]} onClick={() => requestToggle(s)} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <ConfirmToggleDialog
        strategy={pending}
        nextState={pending ? !toggles[pending.id] : false}
        onConfirm={confirmToggle}
        onCancel={() => setPending(null)}
      />
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

  // Strategies is a self-contained, localStorage-only panel with no backend
  // dependency at all — it must never wait on the settings/agent-status
  // fetch below. Rendered unconditionally, above the loading gate, so a
  // slow or unresponsive backend (confirmed live 2026-08-06: apiFetch had
  // no timeout and could hang the whole page forever, fixed in api/client.js)
  // can no longer hide a section that never needed that data in the first place.
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

      {/* Strategies — always visible, never blocked by the backend fetch below */}
      <StrategyExecutionPanel />
      <StrategiesPanel />

      {loading ? (
        <LoadingSpinner message="Loading settings…" />
      ) : (
        <>

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

        </>
      )}

    </div>
  );
}
