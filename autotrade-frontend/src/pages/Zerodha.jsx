import { useState, useEffect, useCallback, useRef } from 'react';
import toast from 'react-hot-toast';
import {
  Wifi, WifiOff, RefreshCw, ExternalLink, LogOut,
  TrendingUp, TrendingDown, IndianRupee, Zap,
  CheckCircle, Clock, XCircle, MinusCircle, AlertTriangle,
} from 'lucide-react';
import {
  getZerodhaStatus, getZerodhaLoginUrl, logoutZerodha,
  getZerodhaHoldings, getZerodhaOrders, getZerodhaTrades,
  getZerodhaPnl, getZerodhaLivePrices, getZerodhaWatchlistAnalysis,
  getZerodhaDeepAnalysis,
} from '../api/client';

// ── Formatters ────────────────────────────────────────────────────────────────

const inr = (n, dec = 2) => {
  if (n == null) return '—';
  return '₹' + Number(n).toLocaleString('en-IN', {
    minimumFractionDigits: dec, maximumFractionDigits: dec,
  });
};

const pct = (n) => {
  if (n == null) return '—';
  const v = Number(n);
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
};

const pos = (n) => Number(n) >= 0;

function PnlCell({ value, showPct, pctValue }) {
  if (value == null) return <span className="text-muted">—</span>;
  return (
    <div>
      <span className={pos(value) ? 'text-emerald-400' : 'text-rose-400'}>
        {pos(value) ? '+' : ''}{inr(value)}
      </span>
      {showPct && pctValue != null && (
        <div className={`text-[11px] ${pos(pctValue) ? 'text-emerald-400/70' : 'text-rose-400/70'}`}>
          {pct(pctValue)}
        </div>
      )}
    </div>
  );
}

function Badge({ status }) {
  const map = {
    COMPLETE:  { color: 'bg-emerald-500/15 text-emerald-400', icon: CheckCircle },
    OPEN:      { color: 'bg-blue-500/15 text-blue-400',       icon: Clock },
    PENDING:   { color: 'bg-blue-500/15 text-blue-400',       icon: Clock },
    CANCELLED: { color: 'bg-slate-500/15 text-slate-400',     icon: MinusCircle },
    REJECTED:  { color: 'bg-rose-500/15 text-rose-400',       icon: XCircle },
  };
  const cfg = map[status] || { color: 'bg-slate-500/15 text-slate-400', icon: MinusCircle };
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${cfg.color}`}>
      <Icon size={10} />
      {status}
    </span>
  );
}

// ── Section 1 — Connection Status ─────────────────────────────────────────────

function ConnectionCard({ status, onConnect, onDisconnect, redirectUrl }) {
  const [showDebug, setShowDebug] = useState(false);

  if (!status) return null;

  if (status.connected) {
    return (
      <div className="rounded-xl border border-emerald-500/20 p-5"
        style={{ background: 'rgba(16,185,129,0.06)' }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-emerald-500/15">
              <Wifi size={18} className="text-emerald-400" />
            </div>
            <div>
              <p className="text-emerald-400 font-bold text-base">Zerodha Connected</p>
              <p className="text-slate-400 text-sm mt-0.5">
                {status.user_name} · {status.user_id} · {status.email}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="text-muted text-xs">Available Cash</p>
              <p className="text-slate-100 font-bold text-lg">{inr(status.available_margins_inr)}</p>
              <p className="text-muted text-xs mt-0.5">Token expires: {status.token_expires_at}</p>
            </div>
            <button onClick={onDisconnect}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-rose-500/30 text-rose-400 text-sm hover:bg-rose-500/10 transition-all">
              <LogOut size={13} />
              Disconnect
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Not connected — show why
  const hasError = Boolean(status.error && status.error !== 'No access token — please login');
  const tokenPresent = status.access_token_present;

  return (
    <div className="rounded-xl border border-amber-500/20 p-5"
      style={{ background: 'rgba(245,158,11,0.06)' }}>
      <div className="flex items-start gap-4">
        <div className="p-2 rounded-lg bg-amber-500/15 shrink-0">
          <WifiOff size={18} className="text-amber-400" />
        </div>
        <div className="flex-1">
          <p className="text-amber-400 font-bold text-base mb-1">Zerodha Not Connected</p>

          {/* Error reason — most useful diagnostic */}
          {hasError && (
            <div className="flex items-start gap-2 p-3 rounded-lg border border-rose-500/20 bg-rose-500/10 mb-3">
              <AlertTriangle size={13} className="text-rose-400 shrink-0 mt-0.5" />
              <p className="text-rose-300 text-xs font-mono break-all">{status.error}</p>
            </div>
          )}

          {tokenPresent && !status.connected && (
            <p className="text-slate-500 text-xs mb-3">
              Access token is saved but Zerodha profile verification failed.
              The token may have expired or the API credentials are incorrect.
            </p>
          )}

          {!tokenPresent && !hasError && (
            <p className="text-slate-400 text-sm mb-4">
              Connect your Zerodha account to see your real portfolio, live prices, and enable real trading.
            </p>
          )}

          {status.api_key_configured === false ? (
            <p className="text-muted text-sm">
              Set <code className="text-cyan bg-white/5 px-1 rounded">ZERODHA_API_KEY</code> and{' '}
              <code className="text-cyan bg-white/5 px-1 rounded">ZERODHA_API_SECRET</code> in{' '}
              <code className="text-cyan bg-white/5 px-1 rounded">.env</code> first.
            </p>
          ) : (
            <>
              <button onClick={onConnect}
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-blue-600 to-cyan-600 text-white font-semibold text-sm hover:opacity-90 transition-all mb-4">
                <ExternalLink size={14} />
                Connect Zerodha Account
              </button>
              <ol className="text-slate-500 text-xs space-y-1 mb-4">
                <li>1. Click the button above to open Zerodha login</li>
                <li>2. Log in with your Zerodha ID and TOTP</li>
                <li>3. You will be redirected back automatically</li>
                <li>4. This page will refresh when connected</li>
              </ol>

              {/* Setup checklist */}
              <button
                onClick={() => setShowDebug(d => !d)}
                className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-1 transition-colors"
              >
                <RefreshCw size={10} />
                {showDebug ? 'Hide' : 'Show'} setup info &amp; diagnostics
              </button>

              {showDebug && (
                <div className="mt-3 space-y-3">
                  {/* Redirect URL for Zerodha developer console */}
                  <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/20">
                    <p className="text-blue-300 text-xs font-semibold mb-1">
                      Required: Redirect URL in Zerodha Developer Console
                    </p>
                    <code className="text-cyan text-xs break-all">
                      {redirectUrl || 'http://localhost:8000/api/v1/zerodha/callback'}
                    </code>
                    <p className="text-slate-500 text-[11px] mt-1">
                      Go to kite.zerodha.com → My Apps → your app → set this exact URL as the Redirect URL.
                    </p>
                  </div>

                  {/* Raw status JSON */}
                  <div className="p-3 rounded-lg bg-white/5 border border-white/10">
                    <p className="text-slate-400 text-xs font-semibold mb-1">Raw status response</p>
                    <pre className="text-[11px] text-slate-400 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(status, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Section 2 — Portfolio Summary ─────────────────────────────────────────────

function PortfolioSummary({ pnl }) {
  if (!pnl) return null;
  const cards = [
    { label: 'Real Portfolio Value', value: inr(pnl.total_equity), sub: null },
    { label: 'Invested (Demat)',     value: inr(pnl.demat_invested), sub: null },
    { label: 'Unrealised P&L',       value: inr(pnl.demat_pnl),    sub: pct(pnl.demat_pnl_pct),   gain: pos(pnl.demat_pnl) },
    { label: "Today's P&L",          value: inr(pnl.today_pnl),     sub: null,                     gain: pos(pnl.today_pnl) },
  ];
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map(({ label, value, sub, gain }) => (
        <div key={label} className="card p-4">
          <p className="text-muted text-xs mb-2">{label}</p>
          <p className={`text-xl font-bold ${gain != null ? (gain ? 'text-emerald-400' : 'text-rose-400') : 'text-slate-100'}`}>
            {value}
          </p>
          {sub && <p className={`text-xs mt-0.5 ${gain ? 'text-emerald-400/70' : 'text-rose-400/70'}`}>{sub}</p>}
        </div>
      ))}
    </div>
  );
}

// ── Section 2 — Holdings table ─────────────────────────────────────────────

function HoldingsTable({ holdings, loading }) {
  if (loading) return <div className="card p-8 text-center text-muted text-sm">Loading holdings…</div>;
  if (!holdings?.length) {
    return (
      <div className="card p-8 text-center">
        <p className="text-muted text-sm">No Demat holdings found.</p>
        <p className="text-muted/60 text-xs mt-1">Holdings sync from Zerodha once connected.</p>
      </div>
    );
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
            {['Stock','Qty','Avg Price','LTP','Day Change','P&L','P&L %'].map(h => (
              <th key={h} className={`px-4 py-3 ${h === 'Stock' ? 'text-left' : 'text-right'}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {holdings.map((h, i) => (
            <tr key={i} className="hover:bg-white/[0.02] transition-colors">
              <td className="px-4 py-3">
                <div className="font-semibold text-slate-200">{h.tradingsymbol}</div>
                <div className="text-xs text-muted">{h.exchange}</div>
              </td>
              <td className="px-4 py-3 text-right text-slate-300">{h.quantity}</td>
              <td className="px-4 py-3 text-right text-slate-300">{inr(h.average_price)}</td>
              <td className="px-4 py-3 text-right font-medium text-slate-100">{inr(h.last_price)}</td>
              <td className="px-4 py-3 text-right">
                <span className={pos(h.day_change) ? 'text-emerald-400' : 'text-rose-400'}>
                  {pct(h.day_change_percentage)}
                </span>
              </td>
              <td className="px-4 py-3 text-right">
                <PnlCell value={h.pnl} />
              </td>
              <td className="px-4 py-3 text-right">
                <PnlCell value={h.pnl_percentage} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Section 3 — Orders ────────────────────────────────────────────────────────

function OrdersTable({ orders }) {
  if (!orders?.length) {
    return <div className="card p-6 text-center text-muted text-sm">No orders today.</div>;
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
            {['Symbol','Type','Qty','Price','Status','Time'].map(h => (
              <th key={h} className={`px-4 py-3 ${h === 'Symbol' ? 'text-left' : 'text-right'}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {orders.map((o, i) => (
            <tr key={i} className="hover:bg-white/[0.02] transition-colors">
              <td className="px-4 py-3 font-semibold text-slate-200">{o.tradingsymbol}</td>
              <td className="px-4 py-3 text-right">
                <span className={o.transaction_type === 'BUY' ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
                  {o.transaction_type}
                </span>
              </td>
              <td className="px-4 py-3 text-right text-slate-300">{o.quantity}</td>
              <td className="px-4 py-3 text-right text-slate-300">{inr(o.price || o.average_price)}</td>
              <td className="px-4 py-3 text-right"><Badge status={o.status} /></td>
              <td className="px-4 py-3 text-right text-muted text-xs">
                {o.order_timestamp ? new Date(o.order_timestamp).toLocaleTimeString('en-IN') : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Section 4 — Paper mode warning ───────────────────────────────────────────

function PaperModeBanner() {
  return (
    <div className="flex items-start gap-3 p-4 rounded-xl border border-amber-500/25"
      style={{ background: 'rgba(245,158,11,0.06)' }}>
      <AlertTriangle size={16} className="text-amber-400 shrink-0 mt-0.5" />
      <div>
        <p className="text-amber-400 font-semibold text-sm">AutoTrade Pro is in PAPER TRADING mode</p>
        <p className="text-slate-400 text-xs mt-1">
          Your Zerodha account is connected for <strong>viewing only</strong>.
          No real orders will be placed automatically.
          To enable real trading, set <code className="bg-white/5 px-1 rounded">PAPER_MODE=false</code> in Settings.
        </p>
      </div>
    </div>
  );
}

// ── Section 5 — Live Prices ───────────────────────────────────────────────────

function LivePricesPanel({ prices }) {
  const entries = Object.entries(prices || {});
  if (!entries.length) {
    return (
      <div className="card p-6 text-center text-muted text-sm">
        Live prices stream once WebSocket connects or during NSE market hours.
      </div>
    );
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
            <th className="px-4 py-3 text-left">Symbol</th>
            <th className="px-4 py-3 text-right">LTP</th>
            <th className="px-4 py-3 text-right">Open</th>
            <th className="px-4 py-3 text-right">High</th>
            <th className="px-4 py-3 text-right">Low</th>
            <th className="px-4 py-3 text-right">Volume</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {entries.map(([sym, d]) => (
            <tr key={sym} className="hover:bg-white/[0.02] transition-colors">
              <td className="px-4 py-3 font-semibold text-slate-200">
                {sym.replace('.NS', '')}
              </td>
              <td className="px-4 py-3 text-right text-slate-100 font-medium">{inr(d.price)}</td>
              <td className="px-4 py-3 text-right text-muted">{inr(d.open)}</td>
              <td className="px-4 py-3 text-right text-emerald-400">{inr(d.high)}</td>
              <td className="px-4 py-3 text-right text-rose-400">{inr(d.low)}</td>
              <td className="px-4 py-3 text-right text-muted">
                {d.volume != null ? Number(d.volume).toLocaleString('en-IN') : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Section 6 — Watchlist & Deep Analysis ────────────────────────────────────

const SIGNAL_CFG = {
  STRONG_BUY:  { color: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30', short: '▲▲ Strong Buy',  bg: 'rgba(16,185,129,0.08)', border: '#10b98133' },
  BUY:         { color: 'bg-teal-500/15 text-teal-300 border-teal-500/30',          short: '▲ Buy',          bg: 'rgba(20,184,166,0.08)', border: '#14b8a633' },
  NEUTRAL:     { color: 'bg-slate-500/15 text-slate-400 border-slate-500/30',       short: '— Neutral',      bg: 'rgba(100,116,139,0.06)', border: '#64748b33' },
  SELL:        { color: 'bg-amber-500/15 text-amber-300 border-amber-500/30',       short: '▼ Sell',         bg: 'rgba(245,158,11,0.08)', border: '#f59e0b33' },
  STRONG_SELL: { color: 'bg-rose-500/15 text-rose-300 border-rose-500/30',          short: '▼▼ Strong Sell', bg: 'rgba(244,63,94,0.08)',  border: '#f43f5e33' },
};

const TREND_COLOR = {
  STRONG_BULL: 'text-emerald-400', BULL: 'text-teal-400',
  NEUTRAL: 'text-slate-400',
  BEAR: 'text-amber-400', STRONG_BEAR: 'text-rose-400',
};

const STORAGE_KEY = 'zerodha_watchlist_v1';

function ScoreBar({ score }) {
  if (score == null) return <span className="text-muted">—</span>;
  const pct   = Math.min(100, Math.max(0, (score + 100) / 2));
  const color = score >= 25 ? 'bg-emerald-500' : score >= -25 ? 'bg-slate-500' : 'bg-rose-500';
  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <div className="flex-1 h-1.5 rounded-full bg-white/10 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs tabular-nums font-mono ${score >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
        {score >= 0 ? '+' : ''}{score}
      </span>
    </div>
  );
}

// Renders bold text from **text** markdown
function Md({ text }) {
  if (!text) return null;
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('**') && p.endsWith('**')
          ? <strong key={i} className="text-slate-200 font-semibold">{p.slice(2, -2)}</strong>
          : <span key={i}>{p}</span>
      )}
    </>
  );
}

function DeepPanel({ data, loading }) {
  if (loading) {
    return (
      <div className="p-8 text-center border-t border-border bg-[#060d1a]">
        <RefreshCw size={16} className="animate-spin text-cyan mx-auto mb-2" />
        <p className="text-muted text-sm">Fetching deep analysis, news &amp; AI commentary…</p>
      </div>
    );
  }
  if (!data) return null;

  const sigCfg = SIGNAL_CFG[data.signal] || SIGNAL_CFG.NEUTRAL;
  const s      = data.trade_setup || {};
  const r      = data.reasoning   || {};
  const ind    = data.indicators  || {};

  const BulletList = ({ items, color }) => (
    items?.length ? (
      <ul className="space-y-1.5">
        {items.map((it, i) => (
          <li key={i} className="flex items-start gap-2 text-xs text-slate-400 leading-relaxed">
            <span className={`mt-0.5 shrink-0 text-[10px] ${color}`}>●</span>
            <span>{it}</span>
          </li>
        ))}
      </ul>
    ) : <p className="text-slate-600 text-xs italic">None</p>
  );

  return (
    <div className="border-t border-border" style={{ background: '#060d1a' }}>
      <div className="p-5 space-y-5">

        {/* AI summary */}
        {data.ai_summary && (
          <div className="p-4 rounded-xl border" style={{ background: sigCfg.bg, borderColor: sigCfg.border }}>
            <p className="text-[10px] font-bold uppercase tracking-widest text-slate-500 mb-2">AI Analysis</p>
            <p className="text-slate-300 text-sm leading-relaxed">{data.ai_summary}</p>
          </div>
        )}

        {/* Trade setup + When to buy/sell */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

          {/* Key levels */}
          <div className="card p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-muted mb-3">Key Levels</p>
            <div className="space-y-2 text-xs">
              {[
                { label: 'Entry Zone',  val: `₹${s.entry_low} – ₹${s.entry_high}`,  color: 'text-cyan' },
                { label: 'Stop Loss',   val: `₹${s.stop_loss} (${s.stop_loss_pct}%)`, color: 'text-rose-400' },
                { label: 'Target 1',    val: `₹${s.target_1} (+${s.target_1_pct}%)`,  color: 'text-emerald-400' },
                { label: 'Target 2',    val: `₹${s.target_2} (+${s.target_2_pct}%)`,  color: 'text-emerald-300' },
                { label: 'Risk/Reward', val: `${s.risk_reward}x`,                      color: s.risk_reward >= 2 ? 'text-emerald-400' : 'text-amber-400' },
                { label: 'Support',     val: `₹${s.support}`,                          color: 'text-slate-300' },
                { label: 'Resistance',  val: `₹${s.resistance}`,                       color: 'text-slate-300' },
              ].map(({ label, val, color }) => (
                <div key={label} className="flex justify-between items-center">
                  <span className="text-muted">{label}</span>
                  <span className={`font-semibold tabular-nums ${color}`}>{val}</span>
                </div>
              ))}
            </div>
          </div>

          {/* When to buy */}
          <div className="card p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-500/70 mb-3">When to Buy</p>
            <p className="text-xs text-slate-400 leading-relaxed whitespace-pre-line">
              <Md text={s.when_to_buy} />
            </p>
          </div>

          {/* When to sell + hold */}
          <div className="card p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-amber-500/70 mb-3">When to Sell / Hold</p>
            <p className="text-xs text-slate-400 leading-relaxed whitespace-pre-line mb-3">
              <Md text={s.when_to_sell} />
            </p>
            {s.hold_strategy && (
              <>
                <p className="text-[10px] font-bold uppercase tracking-widest text-slate-600 mb-1.5">Hold Strategy</p>
                <p className="text-xs text-slate-500 leading-relaxed">{s.hold_strategy}</p>
              </>
            )}
          </div>
        </div>

        {/* Technical Reasons */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="card p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-500/70 mb-3">
              Bullish Factors ({r.bullish?.length || 0})
            </p>
            <BulletList items={r.bullish} color="text-emerald-500" />
          </div>
          <div className="card p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-rose-500/70 mb-3">
              Bearish Factors ({r.bearish?.length || 0})
            </p>
            <BulletList items={r.bearish} color="text-rose-500" />
          </div>
          <div className="card p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-slate-500 mb-3">
              Neutral / Context ({r.neutral?.length || 0})
            </p>
            <BulletList items={r.neutral} color="text-slate-500" />
          </div>
        </div>

        {/* Indicator snapshot */}
        <div className="card p-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-muted mb-3">Indicator Snapshot</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            {[
              { label: 'RSI',       val: ind.rsi != null ? ind.rsi.toFixed(1) : '—',  tag: ind.rsi_signal },
              { label: 'MACD',      val: ind.macd_cross?.replace('_', ' ') || '—',     tag: null },
              { label: 'EMA Trend', val: ind.ema_trend?.replace('_', ' ') || '—',      tag: null },
              { label: 'Supertrend',val: ind.supertrend_dir || '—',                     tag: null },
              { label: 'ADX',       val: ind.adx != null ? ind.adx.toFixed(1) : '—',   tag: ind.adx_strength },
              { label: 'Stoch %K',  val: ind.stoch_k != null ? ind.stoch_k.toFixed(1) : '—', tag: ind.stoch_signal },
            ].map(({ label, val, tag }) => (
              <div key={label} className="bg-white/[0.03] rounded-lg px-3 py-2.5 border border-white/5">
                <p className="text-[10px] text-muted mb-1">{label}</p>
                <p className="text-sm font-semibold text-slate-200 leading-none">{val}</p>
                {tag && tag !== 'NEUTRAL' && tag !== 'NONE' && (
                  <p className={`text-[10px] mt-1 ${
                    tag.includes('BUY') || tag.includes('BULL') || tag.includes('OVERSOLD') ? 'text-emerald-400' :
                    tag.includes('SELL') || tag.includes('BEAR') || tag.includes('OVERBOUGHT') ? 'text-rose-400' :
                    'text-slate-500'
                  }`}>{tag}</p>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* News */}
        {data.news?.length > 0 && (
          <div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-muted mb-3">Recent News</p>
            <div className="space-y-2">
              {data.news.map((n, i) => (
                <a key={i} href={n.url} target="_blank" rel="noreferrer"
                  className="block p-3 rounded-lg border border-white/5 bg-white/[0.02] hover:bg-white/[0.04] transition-colors">
                  <p className="text-sm text-slate-200 leading-snug">{n.headline}</p>
                  <div className="flex items-center gap-3 mt-1.5">
                    <span className="text-[11px] text-slate-500">{n.source}</span>
                    {n.published_at && (
                      <span className="text-[11px] text-slate-600">
                        {new Date(n.published_at).toLocaleDateString('en-IN', { month: 'short', day: 'numeric' })}
                      </span>
                    )}
                    {n.summary && <span className="text-[11px] text-slate-500 truncate flex-1">{n.summary}</span>}
                  </div>
                </a>
              ))}
            </div>
          </div>
        )}
        {!data.news?.length && (
          <p className="text-slate-600 text-xs">
            No recent news found. Add <code className="bg-white/5 px-1 rounded">FINNHUB_KEY</code> to .env for stock-specific news.
          </p>
        )}

      </div>
    </div>
  );
}

function WatchlistAnalysis({ connectedHoldings }) {
  const [symbols,     setSymbols]     = useState(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); } catch { return []; }
  });
  const [input,       setInput]       = useState('');
  const [results,     setResults]     = useState([]);
  const [loading,     setLoading]     = useState(false);
  const [source,      setSource]      = useState('');
  const [asOf,        setAsOf]        = useState('');
  const [selected,    setSelected]    = useState(null);   // expanded symbol
  const [deepData,    setDeepData]    = useState({});     // symbol → deep response
  const [deepLoading, setDeepLoading] = useState(null);  // symbol being loaded

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(symbols));
  }, [symbols]);

  useEffect(() => {
    if (!symbols.length) { setResults([]); return; }
    let cancelled = false;
    setLoading(true);
    getZerodhaWatchlistAnalysis(symbols)
      .then(d => { if (!cancelled) { setResults(d.results || []); setSource(d.source || ''); setAsOf(d.as_of || ''); } })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [symbols]);

  function addSymbol() {
    const sym = input.trim().toUpperCase().replace('.NS', '');
    if (!sym || symbols.includes(sym)) { setInput(''); return; }
    setSymbols(prev => [...prev, sym]);
    setInput('');
  }

  function removeSymbol(sym) {
    setSymbols(prev => prev.filter(s => s !== sym));
    setResults(prev => prev.filter(r => r.symbol !== sym));
    if (selected === sym) setSelected(null);
  }

  function addHoldings() {
    const newSyms = (connectedHoldings || []).map(h => h.tradingsymbol).filter(s => s && !symbols.includes(s));
    if (newSyms.length) setSymbols(prev => [...prev, ...newSyms]);
  }

  function refresh() {
    if (!symbols.length) return;
    setLoading(true); setResults([]);
    getZerodhaWatchlistAnalysis(symbols)
      .then(d => { setResults(d.results || []); setSource(d.source || ''); setAsOf(d.as_of || ''); })
      .catch(() => {}).finally(() => setLoading(false));
  }

  async function handleRowClick(sym) {
    if (selected === sym) { setSelected(null); return; }
    setSelected(sym);
    if (deepData[sym]) return;   // already fetched
    setDeepLoading(sym);
    try {
      const d = await getZerodhaDeepAnalysis(sym);
      setDeepData(prev => ({ ...prev, [sym]: d }));
    } catch { /* show empty panel */ }
    finally { setDeepLoading(null); }
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-slate-200 flex items-center gap-2">
            <Zap size={14} className="text-cyan" />
            Watchlist &amp; Deep Analysis
          </h2>
          {source && <span className="text-[10px] px-1.5 py-0.5 rounded border border-white/10 text-slate-500">{source === 'kite' ? 'Kite data' : 'yfinance'}</span>}
          {asOf && <span className="text-[10px] text-slate-600">{new Date(asOf).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}</span>}
        </div>
        <button onClick={refresh} disabled={loading || !symbols.length}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-xs text-slate-400 hover:text-white hover:bg-white/5 disabled:opacity-40 transition-all">
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Add symbol row */}
      <div className="card p-4 mb-4">
        <div className="flex gap-2 mb-3">
          <input value={input} onChange={e => setInput(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && addSymbol()}
            placeholder="Add NSE symbol… (e.g. RELIANCE, TCS, HDFCBANK)"
            className="flex-1 px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan/40"
          />
          <button onClick={addSymbol}
            className="px-4 py-2 rounded-lg bg-cyan/15 border border-cyan/30 text-cyan text-sm font-semibold hover:bg-cyan/25 transition-all">
            Add
          </button>
          {connectedHoldings?.length > 0 && (
            <button onClick={addHoldings}
              className="px-3 py-2 rounded-lg border border-white/10 text-xs text-slate-400 hover:text-white hover:bg-white/5 transition-all whitespace-nowrap">
              + My holdings
            </button>
          )}
        </div>
        {symbols.length === 0 ? (
          <p className="text-slate-600 text-xs text-center py-1">Add NSE symbols — click any row to see full deep analysis, reasons, news &amp; AI commentary. Saved in browser.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {symbols.map(sym => (
              <span key={sym} className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-white/5 border border-white/10 text-xs text-slate-300">
                {sym}
                <button onClick={() => removeSymbol(sym)} className="ml-0.5 text-slate-600 hover:text-rose-400 transition-colors">×</button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Table */}
      {loading && (
        <div className="card p-8 text-center">
          <RefreshCw size={18} className="animate-spin text-cyan mx-auto mb-2" />
          <p className="text-muted text-sm">Computing technical indicators for {symbols.length} symbol{symbols.length !== 1 ? 's' : ''}…</p>
        </div>
      )}

      {!loading && results.length > 0 && (
        <div className="card overflow-hidden">
          <p className="px-4 pt-3 pb-1 text-[11px] text-slate-600">Click any row for full analysis, trade setup, news &amp; AI commentary</p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
                  {['Symbol','LTP','Change','Signal','Score','RSI','Trend','Ichimoku','Support','Resistance'].map(h => (
                    <th key={h} className={`px-4 py-3 ${h === 'Symbol' ? 'text-left' : 'text-right'}`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.map(r => {
                  const isOpen = selected === r.symbol;
                  if (r.error) {
                    return (
                      <tr key={r.symbol} className="border-b border-border/50 opacity-50">
                        <td className="px-4 py-3 text-slate-400 font-semibold">{r.symbol}</td>
                        <td colSpan={9} className="px-4 py-3 text-rose-400 text-xs">{r.error}</td>
                      </tr>
                    );
                  }
                  const sig    = SIGNAL_CFG[r.signal] || SIGNAL_CFG.NEUTRAL;
                  const chgPos = (r.change_pct ?? 0) >= 0;
                  return (
                    <>
                      <tr key={r.symbol}
                        onClick={() => handleRowClick(r.symbol)}
                        className={`border-b border-border/50 cursor-pointer transition-colors ${isOpen ? 'bg-white/[0.04]' : 'hover:bg-white/[0.02]'}`}>
                        <td className="px-4 py-3 font-bold text-slate-100 flex items-center gap-2">
                          <span className={`w-1 h-5 rounded-full ${isOpen ? 'bg-cyan' : 'bg-white/10'}`} />
                          {r.symbol}
                        </td>
                        <td className="px-4 py-3 text-right font-medium text-slate-100">{inr(r.ltp)}</td>
                        <td className={`px-4 py-3 text-right text-xs font-semibold ${chgPos ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {chgPos ? '+' : ''}{r.change_pct?.toFixed(2)}%
                        </td>
                        <td className="px-4 py-3 text-right">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[11px] font-semibold ${sig.color}`}>
                            {sig.short}
                          </span>
                        </td>
                        <td className="px-4 py-3"><ScoreBar score={r.composite_score} /></td>
                        <td className={`px-4 py-3 text-right text-xs ${r.rsi_signal === 'OVERSOLD' ? 'text-emerald-400' : r.rsi_signal === 'OVERBOUGHT' ? 'text-rose-400' : 'text-slate-300'}`}>
                          {r.rsi != null ? r.rsi.toFixed(1) : '—'}
                          {r.rsi_signal !== 'NEUTRAL' && <div className="text-[10px] opacity-70">{r.rsi_signal}</div>}
                        </td>
                        <td className={`px-4 py-3 text-right text-xs font-semibold ${TREND_COLOR[r.ema_trend] || 'text-slate-400'}`}>
                          {r.ema_trend?.replace(/_/g, ' ')}
                        </td>
                        <td className={`px-4 py-3 text-right text-xs font-semibold ${r.ichimoku_signal?.includes('BUY') ? 'text-emerald-400' : r.ichimoku_signal?.includes('SELL') ? 'text-rose-400' : 'text-slate-400'}`}>
                          {r.ichimoku_signal?.replace(/_/g, ' ')}
                        </td>
                        <td className="px-4 py-3 text-right text-xs text-slate-400">{inr(r.support)}</td>
                        <td className="px-4 py-3 text-right text-xs text-slate-400">{inr(r.resistance)}</td>
                      </tr>
                      {isOpen && (
                        <tr key={`${r.symbol}-deep`}>
                          <td colSpan={10} className="p-0">
                            <DeepPanel
                              data={deepData[r.symbol] || null}
                              loading={deepLoading === r.symbol}
                            />
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}


// ── Page ──────────────────────────────────────────────────────────────────────

export default function Zerodha() {
  const [status,      setStatus]      = useState(null);
  const [holdings,    setHoldings]    = useState([]);
  const [orders,      setOrders]      = useState([]);
  const [pnl,         setPnl]         = useState(null);
  const [prices,      setPrices]      = useState({});
  const [loading,     setLoading]     = useState(true);
  const [hlLoading,   setHlLoading]   = useState(false);
  const [redirectUrl, setRedirectUrl] = useState('');

  const wsRef = useRef(null);

  const fetchStatus = useCallback(async () => {
    try {
      const s = await getZerodhaStatus();
      setStatus(s);
      return s;
    } catch { return null; }
  }, []);

  const fetchAll = useCallback(async (connected) => {
    if (!connected) return;
    setHlLoading(true);
    try {
      const [h, o, p, lp] = await Promise.allSettled([
        getZerodhaHoldings(),
        getZerodhaOrders(),
        getZerodhaPnl(),
        getZerodhaLivePrices(),
      ]);
      if (h.status  === 'fulfilled') setHoldings(h.value?.holdings ?? []);
      if (o.status  === 'fulfilled') setOrders(o.value?.orders ?? []);
      if (p.status  === 'fulfilled') setPnl(p.value);
      if (lp.status === 'fulfilled') setPrices(lp.value?.prices ?? {});
    } finally {
      setHlLoading(false);
    }
  }, []);

  // Fast-poll interval ref — used after user clicks Connect
  const fastPollRef = useRef(null);
  const stopFastPoll = useCallback(() => {
    if (fastPollRef.current) {
      clearInterval(fastPollRef.current);
      fastPollRef.current = null;
    }
  }, []);

  // Start a 2-second poll for up to 3 minutes waiting for OAuth callback
  const startFastPoll = useCallback(() => {
    stopFastPoll();
    let attempts = 0;
    fastPollRef.current = setInterval(async () => {
      attempts++;
      try {
        const s = await getZerodhaStatus();
        setStatus(s);
        if (s?.connected) {
          stopFastPoll();
          toast.success(`Zerodha connected — welcome ${s.user_name || ''}!`);
          fetchAll(true);
        }
      } catch { /* ignore */ }
      if (attempts >= 90) stopFastPoll(); // give up after 3 min
    }, 2000);
  }, [stopFastPoll, fetchAll]);

  useEffect(() => {
    const init = async () => {
      await fetchStatus();
      setLoading(false);
    };
    init();
    const id = setInterval(fetchStatus, 20_000);
    return () => { clearInterval(id); stopFastPoll(); };
  }, [fetchStatus, stopFastPoll]);

  // When connected, load portfolio data + start price polling
  useEffect(() => {
    if (!status) return;
    if (status.connected) {
      fetchAll(true);
      const id = setInterval(() => fetchAll(true), 15_000);
      return () => clearInterval(id);
    }
  }, [status?.connected, fetchAll]);

  // Listen for postMessage from the OAuth callback popup
  useEffect(() => {
    const handleMessage = (e) => {
      if (e.data === 'zerodha_connected') {
        stopFastPoll();
        fetchStatus().then((s) => {
          if (s?.connected) {
            toast.success(`Zerodha connected — welcome ${s.user_name || ''}!`);
            fetchAll(true);
          }
        });
      } else if (typeof e.data === 'string' && e.data.startsWith('zerodha_error:')) {
        stopFastPoll();
        const errMsg = e.data.slice('zerodha_error:'.length);
        toast.error(`Zerodha login failed: ${errMsg}`, { duration: 8000 });
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [fetchStatus, fetchAll, stopFastPoll]);

  async function handleConnect() {
    try {
      const res = await getZerodhaLoginUrl();
      if (res.redirect_url) setRedirectUrl(res.redirect_url);
      // Do NOT use 'noopener' — the popup needs window.opener to send postMessage back
      window.open(res.url, 'zerodha_login', 'width=600,height=700,left=200,top=100');
      toast('Complete login in the popup window…');
      startFastPoll(); // also poll in case postMessage is blocked
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not fetch login URL — check ZERODHA_API_KEY in .env');
    }
  }

  async function handleDisconnect() {
    try {
      await logoutZerodha();
      toast.success('Disconnected from Zerodha');
      setStatus(null);
      setHoldings([]);
      setOrders([]);
      setPnl(null);
      setPrices({});
      await fetchStatus();
    } catch {
      toast.error('Disconnect failed');
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw size={22} className="animate-spin text-cyan" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-7xl">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 flex items-center gap-2">
            <Zap size={22} className="text-cyan" />
            Zerodha KiteConnect
          </h1>
          <p className="text-muted text-sm mt-0.5">Real portfolio, live prices, and order management</p>
        </div>
        {status?.connected && (
          <button onClick={() => fetchAll(true)} disabled={hlLoading}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-white/10 text-sm text-slate-300 hover:text-white hover:bg-white/5 disabled:opacity-50 transition-all">
            <RefreshCw size={13} className={hlLoading ? 'animate-spin' : ''} />
            Refresh
          </button>
        )}
      </div>

      {/* Section 4 — always visible paper mode warning */}
      <PaperModeBanner />

      {/* Section 1 — Connection card */}
      <ConnectionCard
        status={status}
        onConnect={handleConnect}
        onDisconnect={handleDisconnect}
        redirectUrl={redirectUrl}
      />

      {status?.connected && (
        <>
          {/* Section 2 — Portfolio */}
          <PortfolioSummary pnl={pnl} />

          <div>
            <h2 className="text-base font-semibold text-slate-200 mb-3 flex items-center gap-2">
              <IndianRupee size={14} className="text-cyan" />
              Demat Holdings
            </h2>
            <HoldingsTable holdings={holdings} loading={hlLoading && !holdings.length} />
          </div>

          {/* Section 3 — Orders */}
          <div>
            <h2 className="text-base font-semibold text-slate-200 mb-3 flex items-center gap-2">
              {orders.some(o => o.transaction_type === 'BUY')
                ? <TrendingUp size={14} className="text-emerald-400" />
                : <TrendingDown size={14} className="text-rose-400" />}
              Today's Orders
              <span className="ml-1 text-xs text-muted">({orders.length})</span>
            </h2>
            <OrdersTable orders={orders} />
          </div>

          {/* Section 5 — Live prices */}
          <div>
            <h2 className="text-base font-semibold text-slate-200 mb-3 flex items-center gap-2">
              <Zap size={14} className="text-cyan" />
              Live Prices
              <span className="ml-1 text-xs text-muted/60">(WebSocket / REST)</span>
            </h2>
            <LivePricesPanel prices={prices} />
          </div>
        </>
      )}

      {/* Section 6 — Watchlist & Analysis — always visible */}
      <div className="border-t border-border pt-6">
        <WatchlistAnalysis connectedHoldings={status?.connected ? holdings : []} />
      </div>

    </div>
  );
}
