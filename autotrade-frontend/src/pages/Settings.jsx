import { useState, useEffect } from 'react';
import { Save, Plus, X, Settings as SettingsIcon, AlertTriangle } from 'lucide-react';
import toast from 'react-hot-toast';
import LoadingSpinner from '../components/LoadingSpinner';
import { getSettings, saveSettings } from '../api/client';

const DEFAULT_SETTINGS = {
  starting_balance:    1000,
  max_position_size:   10,
  stop_loss_pct:       2,
  take_profit_pct:     4,
  max_daily_loss_pct:  5,
  max_open_positions:  5,
  watchlist:           ['BTC/USD', 'ETH/USD', 'SOL/USD'],
};

function FieldRow({ label, hint, children }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 py-4 border-b border-border last:border-0">
      <div className="sm:w-64 shrink-0">
        <p className="text-slate-300 text-sm font-medium">{label}</p>
        {hint && <p className="text-muted text-xs mt-0.5">{hint}</p>}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  );
}

function NumberInput({ value, onChange, min, max, step = 1, prefix, suffix }) {
  return (
    <div className="flex items-center gap-2 max-w-48">
      {prefix && <span className="text-muted text-sm">{prefix}</span>}
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        step={step}
        className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 tabular-nums focus:outline-none focus:border-accent"
      />
      {suffix && <span className="text-muted text-sm">{suffix}</span>}
    </div>
  );
}

function WatchlistEditor({ symbols, onChange }) {
  const [input, setInput] = useState('');

  const add = () => {
    const sym = input.trim().toUpperCase();
    if (!sym || symbols.includes(sym)) return;
    onChange([...symbols, sym]);
    setInput('');
  };

  const remove = (sym) => onChange(symbols.filter((s) => s !== sym));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {symbols.map((sym) => (
          <span
            key={sym}
            className="inline-flex items-center gap-1.5 bg-accent/15 border border-accent/30 text-accent text-xs font-mono px-2.5 py-1 rounded-lg"
          >
            {sym}
            <button
              onClick={() => remove(sym)}
              className="hover:text-loss transition-colors"
              aria-label={`Remove ${sym}`}
            >
              <X size={11} />
            </button>
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="Add symbol e.g. BTC/USD"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          className="bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-muted focus:outline-none focus:border-accent w-52"
        />
        <button
          onClick={add}
          className="flex items-center gap-1.5 px-3 py-2 bg-accent/20 hover:bg-accent/30 border border-accent/40 text-accent rounded-lg text-sm transition-colors"
        >
          <Plus size={14} />
          Add
        </button>
      </div>
    </div>
  );
}

export default function Settings() {
  const [cfg, setCfg]         = useState(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);

  useEffect(() => {
    getSettings()
      .then((d) => setCfg({ ...DEFAULT_SETTINGS, ...d }))
      .catch(() => setCfg(DEFAULT_SETTINGS))
      .finally(() => setLoading(false));
  }, []);

  const set = (key) => (val) => setCfg((c) => ({ ...c, [key]: val }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await saveSettings(cfg);
      toast.success('Settings saved');
    } catch {
      toast.error('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <LoadingSpinner message="Loading settings…" />;

  return (
    <div className="space-y-6 max-w-2xl">

      {/* Paper mode reminder */}
      <div className="flex items-start gap-3 bg-warn/10 border border-warn/30 rounded-xl p-4">
        <AlertTriangle size={16} className="text-warn mt-0.5 shrink-0" />
        <p className="text-warn/90 text-xs leading-relaxed">
          These settings apply to the <strong>paper trading simulation only</strong>.
          No real money is at risk. Changes take effect on the next simulation cycle.
        </p>
      </div>

      {/* Simulation Parameters */}
      <div className="bg-panel border border-border rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
          <SettingsIcon size={16} className="text-accent" />
          <h2 className="text-slate-200 font-semibold text-sm">Simulation Parameters</h2>
        </div>
        <div className="px-5 divide-y divide-border/50">
          <FieldRow label="Starting Balance" hint="Virtual capital allocated to the simulation">
            <NumberInput value={cfg.starting_balance} onChange={set('starting_balance')} min={100} step={100} prefix="$" />
          </FieldRow>
          <FieldRow label="Max Position Size" hint="Max % of portfolio per single trade">
            <NumberInput value={cfg.max_position_size} onChange={set('max_position_size')} min={1} max={100} step={1} suffix="%" />
          </FieldRow>
          <FieldRow label="Stop Loss" hint="Automatic loss limit per trade">
            <NumberInput value={cfg.stop_loss_pct} onChange={set('stop_loss_pct')} min={0.1} max={50} step={0.1} suffix="%" />
          </FieldRow>
          <FieldRow label="Take Profit" hint="Target profit per trade">
            <NumberInput value={cfg.take_profit_pct} onChange={set('take_profit_pct')} min={0.1} max={200} step={0.1} suffix="%" />
          </FieldRow>
          <FieldRow label="Max Daily Loss" hint="Stop trading for the day once this loss is hit">
            <NumberInput value={cfg.max_daily_loss_pct} onChange={set('max_daily_loss_pct')} min={0.1} max={50} step={0.1} suffix="%" />
          </FieldRow>
          <FieldRow label="Max Open Positions" hint="Maximum concurrent open trades">
            <NumberInput value={cfg.max_open_positions} onChange={set('max_open_positions')} min={1} max={20} step={1} />
          </FieldRow>
        </div>
      </div>

      {/* Watchlist */}
      <div className="bg-panel border border-border rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
          <SettingsIcon size={16} className="text-accent" />
          <h2 className="text-slate-200 font-semibold text-sm">Watchlist</h2>
          <span className="text-muted text-xs">— assets the AI will monitor for signals</span>
        </div>
        <div className="px-5 py-4">
          <WatchlistEditor symbols={cfg.watchlist ?? []} onChange={set('watchlist')} />
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
