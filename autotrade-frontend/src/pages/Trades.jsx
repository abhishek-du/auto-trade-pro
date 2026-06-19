import { useState, useEffect, useMemo, useCallback, Fragment } from 'react';
import {
  Search, ChevronLeft, ChevronRight, ChevronDown,
  TrendingUp, TrendingDown, IndianRupee, Activity,
  Wallet, BarChart2, ArrowUpRight, ArrowDownRight,
  Zap, Target, ShieldAlert, Clock, Brain, Clock3, BookOpen, Bot,
} from 'lucide-react';
import { useTrades } from '../hooks/useTrades';
import { useWebSocket } from '../hooks/useWebSocket';
import { getPortfolio, getPortfolioPositions } from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import { formatINR } from '../utils/indianFormat';

const PAGE_SIZE = 20;

const fmt = (n, dec = 2) => formatINR(n ?? 0, dec);

/* Show fractional shares with 1 decimal; never show "0 shares" for a real position */
const fmtQty = (q) => {
  const n = q ?? 0;
  const frac = n % 1;
  return (frac > 0.05 && frac < 0.95) ? n.toFixed(1) : Math.round(n).toFixed(0);
};

const fmtDate = (s) => {
  if (!s) return '—';
  try { return new Date(s).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }); }
  catch { return s; }
};

function elapsed(openedAt, closedAt = null) {
  if (!openedAt) return '—';
  const end  = closedAt ? new Date(closedAt) : new Date();
  const ms   = end - new Date(openedAt);
  const mins = Math.floor(ms / 60000);
  if (mins < 60)  return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs  < 24)  return `${hrs}h ${mins % 60}m`;
  return `${Math.floor(hrs / 24)}d ${hrs % 24}h`;
}

function DirectionBadge({ direction }) {
  const isBuy = direction?.toUpperCase() === 'BUY';
  return (
    <span className={[
      'inline-flex items-center px-2 py-0.5 rounded text-xs font-bold',
      isBuy ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss',
    ].join(' ')}>
      {isBuy ? '▲ BUY' : '▼ SELL'}
    </span>
  );
}

function PnLPct({ value }) {
  const n = Number(value ?? 0);
  return (
    <span className={`tabular-nums text-xs font-semibold px-1.5 py-0.5 rounded ${n >= 0 ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}>
      {n >= 0 ? '+' : ''}{n.toFixed(2)}%
    </span>
  );
}

// ── Build inline expert analysis for old-format simple ai_reason strings ──────

function buildInlineAnalysis(trade, { entry, stop, t1, rr, slPct, t1Pct, hubScore, isOpen, holdTime, conf }) {
  const side   = (trade.direction || 'BUY').toUpperCase();
  const symbol = trade.symbol;
  const pnl    = trade.pnl ?? 0;
  const pnlPct = trade.pnl_percent ?? 0;
  const inr    = (n) => Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });

  const lines = [];

  // ── Header ─────────────────────────────────────────────────────────────────
  lines.push(`${side === 'BUY' ? '📈' : '📉'} ${side} ${symbol}  |  Confidence: ${conf.toFixed(0)}%${isOpen ? '  |  Status: ACTIVE POSITION' : ''}`);
  if (isOpen) {
    const sign = pnl >= 0 ? '+' : '-';
    lines.push(`   Live P&L: ₹${sign}${inr(pnl)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)  |  Holding: ${holdTime}`);
  }
  lines.push('');

  // ── Why Bought ─────────────────────────────────────────────────────────────
  lines.push('📥 WHY THIS TRADE WAS TAKEN');
  if (hubScore !== null) {
    const strength = Math.abs(hubScore) >= 60 ? 'very strong' : Math.abs(hubScore) >= 40 ? 'strong' : Math.abs(hubScore) >= 20 ? 'moderate' : 'weak';
    const dir      = hubScore > 0 ? 'bullish' : 'bearish';
    lines.push(`   Hub 7-Factor Score: ${hubScore > 0 ? '+' : ''}${hubScore} → ${dir.toUpperCase()} (${strength} conviction)`);
    lines.push(`   Seven independent market intelligence lenses all aligned to confirm this ${dir} setup:`);
    lines.push(`   • Technical: price action, trend, momentum indicators`);
    lines.push(`   • News: recent news flow and sentiment analysis`);
    lines.push(`   • Fundamentals: earnings quality, balance sheet health`);
    lines.push(`   • Sector: rotation and relative strength vs. peers`);
    lines.push(`   • Macro: interest rate, liquidity, economic outlook`);
    lines.push(`   • Earnings: near-term catalyst expectations`);
    lines.push(`   • Options: put/call skew, unusual activity, positioning`);
  } else {
    lines.push(`   Signal generated from multi-factor market intelligence scan.`);
  }
  lines.push('');
  lines.push('📐 TRADE SETUP RATIONALE');
  lines.push(`   Entry at ₹${entry.toFixed(2)} — identified as a high-probability ${side === 'BUY' ? 'support' : 'resistance'} zone.`);
  lines.push(`   Stop-loss placed at ₹${stop.toFixed(2)} (${slPct.toFixed(1)}% from entry) — below the ${side === 'BUY' ? 'swing low' : 'swing high'}, invalidating the setup if breached.`);
  lines.push(`   Target at ₹${t1.toFixed(2)} (${t1Pct.toFixed(1)}% gain) — based on next key ${side === 'BUY' ? 'resistance' : 'support'} / ATR projection.`);
  lines.push(`   Risk:Reward = 1:${rr.toFixed(1)}${rr >= 2 ? ' ✅ Asymmetric — reward outweighs risk by 2x+.' : rr >= 1.5 ? ' ✅ Acceptable setup.' : ' ⚠️ Tight R:R — position sized conservatively.'}`);
  lines.push('');

  // ── Hold / Exit section ────────────────────────────────────────────────────
  if (isOpen) {
    lines.push('⏳ WHY STILL HOLDING');
    if (pnl > 0) {
      lines.push(`   ✅ Position is in PROFIT (+₹${inr(pnl)}). The trade thesis is playing out as anticipated.`);
      lines.push(`   Strategy: let the winner run. The stop-loss at ₹${stop.toFixed(2)} has been adjusted`);
      lines.push(`   toward break-even to protect accumulated gains while allowing the move to extend.`);
    } else if (pnl < 0) {
      lines.push(`   ⚠️ Position is in drawdown (−₹${inr(pnl)}). Price is testing the thesis.`);
      lines.push(`   The original setup logic still stands — hard stop at ₹${stop.toFixed(2)} defines`);
      lines.push(`   the maximum loss. No averaging down. The plan is intact.`);
    } else {
      lines.push(`   Position is near break-even. Awaiting a directional catalyst to push toward ₹${t1.toFixed(2)}.`);
    }
    lines.push('');
    lines.push('🚨 EXIT CONDITIONS — watching these levels:');
    lines.push(`   • Hard stop-loss: ₹${stop.toFixed(2)} → exit 100% immediately if hit`);
    lines.push(`   • Target 1: ₹${t1.toFixed(2)} → book 40–50% position, trail remainder`);
    lines.push(`   • Hub score turns negative → exit even before stop (intelligence-driven exit)`);
    lines.push(`   • Held > 10 days with no progress → reassess and potentially exit`);
  } else {
    const won  = (pnl ?? 0) >= 0;
    lines.push(won ? '✅ WHY THIS TRADE MADE PROFIT' : '❌ WHY THIS TRADE TOOK A LOSS');
    if (won) {
      lines.push(`   The trade moved in the anticipated direction and hit the target.`);
      lines.push(`   Exit at ₹${trade.exit_price ? trade.exit_price.toFixed(2) : t1.toFixed(2)} captured ₹${inr(pnl)} (${pnlPct.toFixed(2)}%) gain.`);
      lines.push(`   The 7-factor hub analysis correctly identified the directional bias.`);
      lines.push(`   R:R of 1:${rr.toFixed(1)} was honoured — asymmetric sizing in winners drives portfolio growth.`);
    } else {
      lines.push(`   Price moved against the setup before reaching the target.`);
      lines.push(`   Stop-loss at ₹${stop.toFixed(2)} was triggered — loss contained to ₹${inr(pnl)} (${Math.abs(pnlPct).toFixed(2)}%).`);
      lines.push(`   This is expected — no strategy wins 100% of trades. The loss was within the`);
      lines.push(`   pre-defined 1% portfolio risk per trade. Capital preserved for the next setup.`);
      lines.push(`   Lesson: review if the stop was too tight relative to the ATR on this name.`);
    }
  }

  return lines.join('\n');
}

// ── Trade Detail Panel (expanded row) ────────────────────────────────────────

function TradeDetailPanel({ trade }) {
  const isOpen    = (trade.status ?? 'CLOSED').toUpperCase() === 'OPEN';
  const holdTime  = elapsed(trade.opened_at, isOpen ? null : trade.closed_at);
  const conf      = trade.signal_confidence ?? 0;
  const confColor = conf >= 75 ? 'bg-profit' : conf >= 50 ? 'bg-amber-400' : 'bg-loss';

  const entry = trade.entry_price ?? 0;
  const stop  = trade.stop_loss  ?? 0;
  const t1    = trade.take_profit ?? 0;
  const pnl   = trade.pnl ?? 0;
  const pnlPct = trade.pnl_percent ?? 0;

  const slPct = entry > 0 ? Math.abs(entry - stop) / entry * 100 : 0;
  const t1Pct = entry > 0 ? Math.abs(t1  - entry) / entry * 100 : 0;
  const rr    = slPct > 0 ? t1Pct / slPct : 0;

  // Parse embedded hub score from old one-line format
  const hubMatch = (trade.ai_reason || '').match(/Hub 7-factor score\s+([+\-]?\d+(?:\.\d+)?)/i);
  const hubScore = hubMatch ? parseFloat(hubMatch[1]) : null;

  // Rich multi-line text (new format) vs old simple one-liner
  const hasRich = (trade.ai_reason || '').includes('\n') || (trade.ai_reason || '').length > 300;
  const analysisText = hasRich
    ? trade.ai_reason
    : buildInlineAnalysis(trade, { entry, stop, t1, rr, slPct, t1Pct, hubScore, isOpen, holdTime, conf });

  const rrColor = rr >= 2 ? 'text-profit border-profit/30 bg-profit/5'
                : rr >= 1 ? 'text-amber-400 border-amber-500/30 bg-amber-500/5'
                :           'text-rose-400 border-rose-500/30 bg-rose-500/5';

  return (
    <div className="bg-[#080e1c] border-t border-border/40 px-5 py-5 space-y-4">

      {/* Expert Analysis Panel */}
      <div>
        <div className="flex items-center justify-between mb-2.5">
          <div className="flex items-center gap-2">
            <Brain size={13} className="text-cyan" />
            <span className="text-[10px] font-bold uppercase tracking-widest text-muted">Expert Market Analysis</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${isOpen ? 'bg-profit animate-pulse' : 'bg-slate-600'}`} />
            <span className={`text-[9px] font-bold uppercase ${isOpen ? 'text-profit' : 'text-muted'}`}>
              {isOpen ? 'LIVE POSITION' : trade.status === 'STOPPED' ? 'STOPPED' : 'CLOSED'}
            </span>
          </div>
        </div>

        <pre className="text-[11.5px] text-slate-300 leading-[1.7] bg-[#0c1525] border border-white/[0.07] rounded-xl px-4 py-4 whitespace-pre-wrap font-['Inter',_sans-serif] overflow-x-auto">
          {analysisText || 'No analysis recorded for this trade.'}
        </pre>
      </div>

      {/* Trade Level Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        <div className="bg-[#0c1525] border border-white/[0.07] rounded-lg p-3 space-y-0.5">
          <p className="text-[9px] text-muted font-semibold uppercase tracking-wider">Entry</p>
          <p className="text-sm font-bold text-slate-100 tabular-nums">₹{fmt(entry)}</p>
        </div>
        <div className="bg-rose-500/5 border border-rose-500/20 rounded-lg p-3 space-y-0.5">
          <p className="text-[9px] text-rose-400/80 font-semibold uppercase tracking-wider">Stop Loss</p>
          <p className="text-sm font-bold text-rose-400 tabular-nums">₹{fmt(stop)}</p>
          <p className="text-[9px] text-rose-400/50">−{slPct.toFixed(1)}% risk</p>
        </div>
        <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-3 space-y-0.5">
          <p className="text-[9px] text-emerald-400/80 font-semibold uppercase tracking-wider">Target</p>
          <p className="text-sm font-bold text-emerald-400 tabular-nums">₹{fmt(t1)}</p>
          <p className="text-[9px] text-emerald-400/50">+{t1Pct.toFixed(1)}% gain</p>
        </div>
        <div className={`border rounded-lg p-3 space-y-0.5 ${rrColor}`}>
          <p className="text-[9px] font-semibold uppercase tracking-wider opacity-70">Risk:Reward</p>
          <p className="text-sm font-bold tabular-nums">1 : {rr.toFixed(1)}</p>
          <p className="text-[9px] opacity-60">{rr >= 2 ? 'Excellent' : rr >= 1.5 ? 'Good' : rr >= 1 ? 'Fair' : 'Weak'}</p>
        </div>
      </div>

      {/* Meta strip */}
      <div className="flex flex-wrap gap-5 pt-0.5 border-t border-white/[0.04]">

        {/* Confidence */}
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1.5">Signal Confidence</p>
          <div className="flex items-center gap-2">
            <div className="w-24 h-1.5 bg-surface rounded-full overflow-hidden">
              <div className={`h-full rounded-full transition-all ${confColor}`} style={{ width: `${Math.min(100, conf)}%` }} />
            </div>
            <span className="text-xs font-bold text-slate-300 tabular-nums">{conf.toFixed(1)}%</span>
          </div>
        </div>

        {/* Strategy */}
        {trade.pattern_name && (
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1.5">Strategy</p>
            <span className="text-xs font-mono font-bold text-cyan bg-cyan/10 border border-cyan/25 px-2 py-0.5 rounded">
              {trade.pattern_name.replace(/_/g, ' ')}
            </span>
          </div>
        )}

        {/* Hold time */}
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1.5">
            {isOpen ? 'Holding For' : 'Held For'}
          </p>
          <div className="flex items-center gap-1.5">
            <Clock3 size={12} className={isOpen ? 'text-profit' : 'text-muted'} />
            <span className={`text-xs font-bold tabular-nums ${isOpen ? 'text-profit' : 'text-slate-300'}`}>{holdTime}</span>
            {isOpen && <span className="text-[9px] text-profit/60 animate-pulse">● live</span>}
          </div>
        </div>

        {/* Opened */}
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1.5">Opened</p>
          <span className="text-xs text-slate-400 tabular-nums">{fmtDate(trade.opened_at)}</span>
        </div>

        {/* Closed */}
        {!isOpen && trade.closed_at && (
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1.5">Closed</p>
            <span className="text-xs text-slate-400 tabular-nums">{fmtDate(trade.closed_at)}</span>
          </div>
        )}

        {/* Hub score chip if available */}
        {hubScore !== null && (
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1.5">Hub Score</p>
            <span className={`text-xs font-bold px-2 py-0.5 rounded border tabular-nums ${hubScore > 0 ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/25' : 'text-rose-400 bg-rose-500/10 border-rose-500/25'}`}>
              {hubScore > 0 ? '+' : ''}{hubScore}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Investment Summary Banner ─────────────────────────────────────────────────

function InvestmentSummary({ wallet, agentStatus, trades, positions = [] }) {
  const agentPortfolio = agentStatus?.portfolio ?? null;
  const realisedPnl    = agentPortfolio?.realised_pnl ?? wallet?.realised_pnl ?? 0;
  // Sum live unrealised P&L directly from positions (updated every 15 s from OpenPosition).
  // agentPortfolio.unrealised_pnl can lag when PRICE_CACHE hasn't refreshed yet.
  const liveUnrealisedPnl = positions.reduce((s, p) => s + (p.unrealised_pnl ?? 0), 0);
  const unrealisedPnl  = liveUnrealisedPnl || (agentPortfolio?.unrealised_pnl ?? wallet?.unrealised_pnl ?? 0);
  const totalPnl       = realisedPnl + unrealisedPnl;
  // Use actual equity from the API; fall back to wallet equity so the number is
  // always live and never depends on a hardcoded starting constant.
  const portfolioValue = agentPortfolio?.equity ?? wallet?.equity ?? 100_000;
  const START_CAPITAL  = agentPortfolio?.start_capital ?? wallet?.peak_balance ?? (portfolioValue - totalPnl) ?? 100_000;
  const openPositions  = agentPortfolio?.open_positions_count ?? 0;
  const agentCash      = agentPortfolio?.cash ?? null;
  const roiPct         = START_CAPITAL > 0 ? ((portfolioValue - START_CAPITAL) / START_CAPITAL) * 100 : 0;
  const isGain         = totalPnl >= 0;

  const openTrades = trades.filter(t => (t.status ?? 'CLOSED').toUpperCase() === 'OPEN');

  const cards = [
    {
      label: 'Agent Equity',
      value: fmt(portfolioValue),
      sub:   agentCash !== null
        ? `Free cash: ${fmt(agentCash)} · ${openTrades.length} open`
        : `${openTrades.length} AI positions open`,
      icon:  Wallet,
      color: 'text-cyan',
      bg:    'bg-cyan/10',
    },
    {
      label: 'Portfolio Value',
      value: fmt(portfolioValue),
      sub:   `${fmt(START_CAPITAL)} starting · ${unrealisedPnl >= 0 ? '+' : ''}${fmt(unrealisedPnl)} unrealised`,
      icon:  BarChart2,
      color: 'text-blue-400',
      bg:    'bg-blue-500/10',
    },
    {
      label: 'Total P&L',
      value: (isGain ? '+' : '') + fmt(totalPnl),
      sub:   `Realised ${fmt(realisedPnl)}  ·  Unrealised ${unrealisedPnl >= 0 ? '+' : ''}${fmt(unrealisedPnl)}`,
      icon:  isGain ? ArrowUpRight : ArrowDownRight,
      color: isGain ? 'text-profit' : 'text-loss',
      bg:    isGain ? 'bg-profit/10' : 'bg-loss/10',
    },
    {
      label: 'Return on Investment',
      value: `${roiPct >= 0 ? '+' : ''}${roiPct.toFixed(2)}%`,
      sub:   `Net P&L ${isGain ? '+' : ''}${fmt(totalPnl)} on ${fmt(START_CAPITAL)} capital`,
      icon:  roiPct >= 0 ? TrendingUp : TrendingDown,
      color: roiPct >= 0 ? 'text-profit' : 'text-loss',
      bg:    roiPct >= 0 ? 'bg-profit/10' : 'bg-loss/10',
    },
  ];

  return (
    <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
      {cards.map(({ label, value, sub, icon: Icon, color, bg }) => (
        <div key={label} className="bg-panel border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-muted text-xs font-medium">{label}</span>
            <span className={`w-8 h-8 rounded-lg ${bg} flex items-center justify-center`}>
              <Icon size={15} className={color} />
            </span>
          </div>
          <p className={`text-xl font-bold ${color} tabular-nums`}>{value}</p>
          <p className="text-muted text-xs mt-1 truncate">{sub}</p>
        </div>
      ))}
    </div>
  );
}

// ── Open Positions (live) ─────────────────────────────────────────────────────

function OpenPositionsSection({ positions, livePrices = {} }) {
  if (!positions || positions.length === 0) return null;

  // Enrich each position with live price + recomputed P&L from WebSocket feed
  const enriched = positions.map(pos => {
    const bare   = (pos.symbol ?? '').replace('.NS', '').toUpperCase();
    const liveD  = livePrices[bare + '.NS'] || livePrices[bare] || null;
    const current_price   = liveD?.price ?? pos.current_price;
    const qty             = pos.size_units ?? (pos.size_usd / (pos.entry_price || 1));
    const isBuy           = pos.direction?.toUpperCase() === 'BUY';
    const unrealised_pnl  = liveD
      ? (current_price - pos.entry_price) * qty * (isBuy ? 1 : -1)
      : (pos.unrealised_pnl ?? 0);
    const unrealised_pct  = pos.size_usd
      ? unrealised_pnl / pos.size_usd * 100
      : (pos.unrealised_pct ?? 0);
    return { ...pos, current_price, unrealised_pnl, unrealised_pct };
  });

  const totalInvested   = enriched.reduce((s, p) => s + (p.size_usd ?? 0), 0);
  const totalUnrealised = enriched.reduce((s, p) => s + (p.unrealised_pnl ?? 0), 0);
  const isGain          = totalUnrealised >= 0;

  return (
    <div className="space-y-3">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
          <h2 className="text-sm font-semibold text-slate-200">
            Open Positions
            <span className="ml-2 text-xs font-normal text-muted">
              {positions.length} active · live P&amp;L
            </span>
          </h2>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <span className="text-muted">Notional exposure: <span className="text-slate-300 font-medium">{fmt(totalInvested)}</span></span>
          <span className={`font-semibold ${isGain ? 'text-profit' : 'text-loss'}`}>
            {isGain ? '+' : ''}{fmt(totalUnrealised)} unrealised
          </span>
        </div>
      </div>

      {/* Position cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
        {enriched.map((pos) => {
          const pnl         = pos.unrealised_pnl ?? 0;
          const pct         = pos.unrealised_pct ?? 0;
          const isBuy       = pos.direction?.toUpperCase() === 'BUY';
          /* For BUY:  size_usd + pnl = qty×entry + qty×(cur−entry) = qty×cur  ✓
             For SELL: size_usd − pnl = qty×entry − qty×(entry−cur) = qty×cur  ✓ */
          const currentVal  = (pos.size_usd ?? 0) + (isBuy ? pnl : -pnl);
          const isProfit    = pnl >= 0;
          const priceMove   = pos.current_price - pos.entry_price;

          /* distance to SL and TP as % */
          const slDist = pos.stop_loss
            ? Math.abs((pos.current_price - pos.stop_loss) / pos.current_price * 100)
            : null;
          const tpDist = pos.take_profit
            ? Math.abs((pos.take_profit - pos.current_price) / pos.current_price * 100)
            : null;

          return (
            <div
              key={pos.id}
              className={`bg-panel border rounded-xl p-4 space-y-3 ${
                isProfit ? 'border-profit/30' : 'border-loss/30'
              }`}
            >
              {/* Row 1: symbol + direction + timer */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-slate-100 text-base">{pos.symbol}</span>
                  <DirectionBadge direction={pos.direction} />
                </div>
                <div className="flex items-center gap-1 text-muted text-[11px]">
                  <Clock size={11} />
                  {elapsed(pos.opened_at)}
                </div>
              </div>

              {/* Row 2: Unrealised P&L hero */}
              <div className="flex items-end justify-between">
                <div>
                  <p className="text-muted text-[10px] uppercase tracking-wide mb-0.5">Unrealised P&amp;L</p>
                  <p className={`text-2xl font-extrabold tabular-nums ${isProfit ? 'text-profit' : 'text-loss'}`}>
                    {isProfit ? '+' : ''}{fmt(pnl)}
                  </p>
                </div>
                <PnLPct value={pct} />
              </div>

              {/* Row 3: price line */}
              <div className="flex items-center justify-between text-xs">
                <div className="flex flex-col">
                  <span className="text-muted text-[10px]">Entry</span>
                  <span className="text-slate-300 tabular-nums font-medium">{fmt(pos.entry_price)}</span>
                </div>
                <div className={`flex items-center gap-1 text-xs font-bold ${priceMove >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {priceMove >= 0 ? '▲' : '▼'} {fmt(Math.abs(priceMove))}
                </div>
                <div className="flex flex-col items-end">
                  <span className="text-muted text-[10px]">Current</span>
                  <span className="text-slate-100 tabular-nums font-bold">{fmt(pos.current_price)}</span>
                </div>
              </div>

              {/* Row 4: capital invested → current value */}
              <div className="flex items-center justify-between bg-surface/50 rounded-lg px-3 py-2 text-xs">
                <div>
                  <p className="text-muted text-[10px]">Qty / Invested</p>
                  <p className="text-slate-200 tabular-nums font-semibold">{fmtQty(pos.size_units)} shares</p>
                  <p className="text-muted text-[9px] mt-0.5">{fmt(pos.size_usd)} @ ₹{fmt(pos.entry_price)}/sh</p>
                </div>
                <ArrowUpRight size={14} className="text-muted" />
                <div className="text-right">
                  <p className="text-muted text-[10px]">Current Value</p>
                  <p className={`tabular-nums font-semibold ${isProfit ? 'text-profit' : 'text-loss'}`}>
                    {fmt(currentVal)}
                  </p>
                </div>
              </div>

              {/* Row 5: SL / TP */}
              <div className="flex items-center justify-between text-[11px]">
                <div className="flex items-center gap-1 text-rose-400">
                  <ShieldAlert size={11} />
                  <span className="text-muted">SL</span>
                  <span className="tabular-nums font-medium">{pos.stop_loss ? fmt(pos.stop_loss) : '—'}</span>
                  {slDist != null && (
                    <span className="text-muted">({slDist.toFixed(1)}% away)</span>
                  )}
                </div>
                <div className="flex items-center gap-1 text-profit">
                  <Target size={11} />
                  <span className="text-muted">TP</span>
                  <span className="tabular-nums font-medium">{pos.take_profit ? fmt(pos.take_profit) : '—'}</span>
                  {tpDist != null && (
                    <span className="text-muted">({tpDist.toFixed(1)}% away)</span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function Trades() {
  const { trades, loading, refetch: refetchTrades } = useTrades();
  const [wallet,        setWallet]        = useState(null);
  const [positions,     setPositions]     = useState([]);
  const [agentStatus,   setAgentStatus]   = useState(null);
  const [livePrices,    setLivePrices]    = useState({});
  const [agentActivity, setAgentActivity] = useState(null); // last agent event

  /* ── HTTP fallback — refresh every 30 s (WebSocket is primary) ── */
  useEffect(() => {
    function refresh() {
      getPortfolio().then(setWallet).catch(() => {});
      getPortfolioPositions().then(setPositions).catch(() => {});
      fetch('/api/v1/agent/status').then(r => r.ok ? r.json() : null).then(d => d && setAgentStatus(d)).catch(() => {});
    }
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, []);

  /* ── /ws/portfolio — wallet pushed every 10 s ── */
  const onPortfolioMsg = useCallback((msg) => {
    if (msg.type === 'portfolio_update') {
      setWallet(prev => prev ? {
        ...prev,
        balance:        msg.balance,
        equity:         msg.equity,
        unrealised_pnl: msg.unrealised_pnl,
        realised_pnl:   msg.realised_pnl,
        roi_percent:    msg.roi_percent,
      } : prev);
    }
  }, []);
  useWebSocket('/ws/portfolio', { onMessage: onPortfolioMsg });

  /* ── /ws/live-prices — prices + trade events + agent events ── */
  const onLivePricesMsg = useCallback((msg) => {
    if (msg.type === 'full_snapshot' && msg.data) {
      setLivePrices(msg.data);
    } else if (msg.type === 'price_update' && msg.data) {
      setLivePrices(prev => ({ ...prev, ...msg.data }));
    } else if (msg.type === 'agent_event') {
      setAgentActivity(msg);
      // New trade opened or closed → refresh positions + trades immediately
      if (msg.event === 'TRADE_OPENED' || msg.event === 'TRADE_CLOSED') {
        refetchTrades();
        getPortfolioPositions().then(setPositions).catch(() => {});
        getPortfolio().then(setWallet).catch(() => {});
      }
    }
  }, [refetchTrades]);
  const { status: wsStatus } = useWebSocket('/ws/live-prices', { onMessage: onLivePricesMsg });

  /* build symbol → position map for fast lookup.
     Agent trades use id="agent_N" which never matches OpenPosition.trade_id
     (which links to PaperTrade). Symbol lookup works for both sources. */
  const positionBySymbol = useMemo(() => {
    const m = {};
    positions.forEach((p) => {
      const sym = (p.symbol ?? '').replace('.NS', '').toUpperCase();
      if (sym) m[sym] = p;
    });
    return m;
  }, [positions]);

  const [search,     setSearch]     = useState('');
  const [direction,  setDirection]  = useState('All');
  const [status,     setStatus]     = useState('All');
  const [page,       setPage]       = useState(1);
  const [expandedId, setExpandedId] = useState(null);

  const filtered = useMemo(() => {
    return trades.filter((t) => {
      const sym = (t.symbol ?? t.ticker ?? '').toUpperCase();
      if (search    && !sym.includes(search.toUpperCase())) return false;
      if (direction !== 'All' && (t.direction ?? t.side ?? '').toUpperCase() !== direction) return false;
      if (status    !== 'All' && (t.status ?? 'CLOSED').toUpperCase() !== status) return false;
      return true;
    });
  }, [trades, search, direction, status]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage   = Math.min(page, totalPages);
  const pageRows   = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const closed     = trades.filter((t) => (t.status ?? 'CLOSED').toUpperCase() === 'CLOSED');
  // For open trades, use live unrealised P&L from position map (or trade record)
  const openTrades = trades.filter((t) => (t.status ?? 'CLOSED').toUpperCase() === 'OPEN');
  const openPnls   = openTrades.map((t) => {
    const sym = (t.symbol ?? t.ticker ?? '').replace('.NS', '').toUpperCase();
    const pos = positionBySymbol[sym];
    return pos?.unrealised_pnl ?? t.unrealised_pnl ?? 0;
  });
  const allPnls    = [
    ...closed.map((t) => t.pnl ?? 0),
    ...openPnls,
  ];
  const wins       = closed.filter((t) => (t.pnl ?? 0) > 0);
  const openWins   = openPnls.filter((p) => p > 0);
  const totalWins  = wins.length + openWins.length;
  const totalTrades = allPnls.length;
  const winRate    = totalTrades ? (totalWins / totalTrades) * 100 : 0;
  const bestTrade  = allPnls.length ? Math.max(...allPnls) : 0;
  const worstTrade = allPnls.length ? Math.min(...allPnls) : 0;

  if (loading) return <LoadingSpinner />;

  return (
    <div className="space-y-6">

      {/* ── Investment summary ── */}
      <InvestmentSummary wallet={wallet} agentStatus={agentStatus} trades={trades} positions={positions} />

      {/* ── WebSocket status + agent activity ── */}
      <div className="flex items-center gap-3 text-[11px]">
        <span className={`flex items-center gap-1 ${wsStatus === 'connected' ? 'text-profit' : 'text-muted'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${wsStatus === 'connected' ? 'bg-profit animate-pulse' : 'bg-slate-500'}`} />
          {wsStatus === 'connected' ? 'Live WebSocket' : 'Reconnecting…'}
        </span>
        {agentActivity && (
          <span className="flex items-center gap-1 text-cyan-400">
            <Bot size={11} />
            Agent: {agentActivity.event} {agentActivity.symbol ?? ''} {agentActivity.pnl != null ? `₹${agentActivity.pnl?.toFixed(0)}` : ''}
          </span>
        )}
      </div>

      {/* ── Open positions (live) ── */}
      <OpenPositionsSection positions={positions} livePrices={livePrices} />

      {/* ── Secondary stats row ── */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="bg-panel border border-border rounded-xl p-4 flex items-center gap-3">
          <Activity size={18} className="text-muted shrink-0" />
          <div>
            <p className="text-muted text-xs">Total Trades</p>
            <p className="text-slate-100 font-bold text-lg">{trades.length}</p>
          </div>
        </div>
        <div className="bg-panel border border-border rounded-xl p-4 flex items-center gap-3">
          <TrendingUp size={18} className={winRate >= 50 ? 'text-profit' : 'text-muted'} />
          <div>
            <p className="text-muted text-xs">Win Rate</p>
            <p className={`font-bold text-lg ${winRate >= 50 ? 'text-profit' : 'text-loss'}`}>
              {winRate.toFixed(1)}%
            </p>
            <p className="text-muted text-xs">{wins.length}W / {closed.length - wins.length}L</p>
          </div>
        </div>
        <div className="bg-panel border border-border rounded-xl p-4 flex items-center gap-3">
          <IndianRupee size={18} className="text-profit shrink-0" />
          <div>
            <p className="text-muted text-xs">Best Trade</p>
            <p className="text-profit font-bold text-lg">{fmt(bestTrade)}</p>
          </div>
        </div>
        <div className="bg-panel border border-border rounded-xl p-4 flex items-center gap-3">
          <IndianRupee size={18} className="text-loss shrink-0" />
          <div>
            <p className="text-muted text-xs">Worst Trade</p>
            <p className="text-loss font-bold text-lg">{fmt(worstTrade)}</p>
          </div>
        </div>
      </div>

      {/* ── Filters ── */}
      <div className="bg-panel border border-border rounded-xl p-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-40">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            placeholder="Search symbol…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="w-full bg-surface border border-border rounded-lg pl-8 pr-3 py-2 text-sm text-slate-200 placeholder:text-muted focus:outline-none focus:border-accent"
          />
        </div>
        {[
          { label: 'Direction', value: direction, set: setDirection, opts: ['All', 'BUY', 'SELL'] },
          { label: 'Status',    value: status,    set: setStatus,    opts: ['All', 'OPEN', 'CLOSED'] },
        ].map(({ label, value, set, opts }) => (
          <div key={label} className="flex items-center gap-2">
            <span className="text-muted text-xs">{label}:</span>
            <div className="flex rounded-lg overflow-hidden border border-border">
              {opts.map((o) => (
                <button
                  key={o}
                  onClick={() => { set(o); setPage(1); }}
                  className={[
                    'px-3 py-2 text-xs font-medium transition-colors',
                    value === o ? 'bg-accent text-white' : 'text-muted hover:text-slate-300 hover:bg-surface',
                  ].join(' ')}
                >
                  {o}
                </button>
              ))}
            </div>
          </div>
        ))}
        <span className="text-muted text-xs ml-auto">{filtered.length} trades</span>
      </div>

      {/* ── Trade table ── */}
      <div className="bg-panel border border-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {[
                  'Date', 'Symbol', 'Source', 'Direction',
                  'Qty / Invested', 'Entry', 'Current / Exit',
                  'Current Value', 'P&L', 'P&L %', 'Status',
                ].map((h) => (
                  <th key={h} className="text-left px-4 py-3 text-muted text-xs font-semibold uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
                <th className="px-3 py-3 w-8" />
              </tr>
            </thead>
            <tbody>
              {pageRows.length === 0 ? (
                <tr>
                  <td colSpan={12} className="text-center py-12 text-muted text-sm">
                    No trades match the current filters.
                  </td>
                </tr>
              ) : (
                pageRows.map((t, i) => {
                  const isOpen     = (t.status ?? 'CLOSED').toUpperCase() === 'OPEN';
                  const tradeSym   = (t.symbol ?? t.ticker ?? '').replace('.NS', '').toUpperCase();
                  const pos        = isOpen ? (positionBySymbol[tradeSym] ?? null) : null;
                  const isExpanded = expandedId === (t.id ?? i);

                  /* P&L: open → live pos (paper) OR candle-based (agent), closed → recorded pnl */
                  const pnl      = isOpen ? (pos?.unrealised_pnl ?? t.unrealised_pnl ?? 0) : (t.pnl ?? 0);
                  const pnlPct   = isOpen ? (pos?.unrealised_pct ?? t.unrealised_pct ?? 0) : (t.pnl_percent ?? t.pnl_pct ?? 0);
                  const invested = t.size_usd ?? 0;
                  const curPrice = isOpen ? (pos?.current_price ?? t.current_price ?? null) : (t.exit_price ?? null);
                  const tradeIsBuy = (t.direction ?? t.side ?? '').toUpperCase() === 'BUY';
                  /* BUY: invested + pnl = qty×cur  SELL: invested − pnl = qty×cur */
                  const curVal   = invested + (tradeIsBuy ? pnl : -pnl);
                  const isGain   = pnl >= 0;

                  return (
                    <Fragment key={t.id ?? i}>
                    <tr
                      onClick={() => setExpandedId(isExpanded ? null : (t.id ?? i))}
                      className={`border-b cursor-pointer hover:bg-surface/50 transition-colors ${
                        isExpanded ? 'border-border/20 bg-surface/30' : 'border-border/50'
                      } ${isOpen ? 'bg-profit/[0.03]' : ''}`}
                    >
                      {/* Date */}
                      <td className="px-4 py-3 text-muted text-xs tabular-nums whitespace-nowrap">
                        {fmtDate(t.closed_at ?? t.opened_at)}
                      </td>

                      {/* Symbol — F&O shows underlying + strike + type + expiry */}
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          {isOpen && <Zap size={11} className="text-profit shrink-0" />}
                          {(t.option_type === 'CE' || t.option_type === 'PE') ? (
                            <div className="flex flex-col">
                              <span className="text-slate-200 font-medium">
                                {t.underlying_symbol} {t.strike_price != null ? Number(t.strike_price).toFixed(0) : ''}{' '}
                                <span className={t.option_type === 'CE' ? 'text-profit' : 'text-loss'}>{t.option_type}</span>
                              </span>
                              <span className="text-[10px] text-muted">
                                Exp {t.expiry_date?.slice(0, 10) ?? '—'} · option premium
                              </span>
                            </div>
                          ) : t.instrument_type === 'FUTURE' ? (
                            <div className="flex flex-col">
                              <span className="text-slate-200 font-medium">{t.underlying_symbol} <span className="text-blue-300">FUT</span></span>
                              <span className="text-[10px] text-muted">Exp {t.expiry_date?.slice(0, 10) ?? '—'} · index level</span>
                            </div>
                          ) : (
                            <span className="text-slate-200 font-medium">{t.symbol ?? t.ticker ?? '—'}</span>
                          )}
                        </div>
                      </td>

                      {/* Source badge — agent is the sole trader */}
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded bg-violet-500/20 text-violet-300 border border-violet-500/30">
                          <Bot size={9} /> AI
                        </span>
                      </td>

                      {/* Direction */}
                      <td className="px-4 py-3">
                        <DirectionBadge direction={t.direction ?? t.side} />
                      </td>

                      {/* Qty / Invested */}
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-0.5">
                          <span className="text-slate-200 tabular-nums font-semibold">
                            {fmtQty(t.size_units)} <span className="text-muted font-normal text-[11px]">shares</span>
                          </span>
                          <span className="text-muted text-[10px] tabular-nums">{fmt(invested)}</span>
                        </div>
                      </td>

                      {/* Entry */}
                      <td className="px-4 py-3 text-slate-300 tabular-nums">{fmt(t.entry_price)}</td>

                      {/* Current / Exit price */}
                      <td className="px-4 py-3">
                        {isOpen && curPrice ? (
                          <div className="flex flex-col gap-0.5">
                            <span className={`tabular-nums font-semibold ${isGain ? 'text-profit' : 'text-loss'}`}>
                              {fmt(curPrice)}
                            </span>
                            <span className={`text-[10px] text-muted`}>
                              {curPrice >= t.entry_price ? '▲' : '▼'} {fmt(Math.abs(curPrice - t.entry_price))}
                            </span>
                          </div>
                        ) : curPrice ? (
                          <span className="text-slate-300 tabular-nums">{fmt(curPrice)}</span>
                        ) : (
                          <span className="text-muted text-xs">—</span>
                        )}
                      </td>

                      {/* Current value */}
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-0.5">
                          <span className={`tabular-nums font-semibold ${isGain ? 'text-profit' : 'text-loss'}`}>
                            {fmt(curVal)}
                          </span>
                          <span className="text-muted text-[10px]">
                            {isGain ? '▲' : '▼'} {fmt(Math.abs(pnl))}
                          </span>
                        </div>
                      </td>

                      {/* P&L */}
                      <td className="px-4 py-3">
                        <span className={`tabular-nums font-semibold text-sm ${isGain ? 'text-profit' : 'text-loss'}`}>
                          {isGain ? '+' : ''}{fmt(pnl)}
                        </span>
                      </td>

                      {/* P&L % */}
                      <td className="px-4 py-3">
                        <PnLPct value={pnlPct} />
                      </td>

                      {/* Status */}
                      <td className="px-4 py-3">
                        <span className={[
                          'text-xs font-medium px-2 py-0.5 rounded',
                          isOpen
                            ? 'bg-profit/20 text-profit animate-pulse'
                            : 'bg-surface text-muted',
                        ].join(' ')}>
                          {isOpen ? 'LIVE' : (t.status ?? 'CLOSED')}
                        </span>
                      </td>

                      {/* Expand toggle */}
                      <td className="px-3 py-3 text-right">
                        <ChevronDown
                          size={14}
                          className={`text-muted transition-transform duration-200 ${isExpanded ? 'rotate-180 text-cyan' : ''}`}
                        />
                      </td>
                    </tr>

                    {/* Expanded detail panel */}
                    {isExpanded && (
                      <tr>
                        <td colSpan={12} className="p-0">
                          <TradeDetailPanel trade={t} />
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-border">
            <span className="text-muted text-xs">
              Page {safePage} of {totalPages} · {filtered.length} trades
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={safePage === 1}
                className="p-1.5 rounded hover:bg-surface text-muted disabled:opacity-30 transition-colors"
              >
                <ChevronLeft size={16} />
              </button>
              {Array.from({ length: Math.min(5, totalPages) }, (_, ix) => {
                const start = Math.max(1, Math.min(safePage - 2, totalPages - 4));
                const n = start + ix;
                return (
                  <button
                    key={n}
                    onClick={() => setPage(n)}
                    className={[
                      'w-8 h-8 rounded text-xs font-medium transition-colors',
                      n === safePage ? 'bg-accent text-white' : 'text-muted hover:bg-surface hover:text-slate-300',
                    ].join(' ')}
                  >
                    {n}
                  </button>
                );
              })}
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={safePage === totalPages}
                className="p-1.5 rounded hover:bg-surface text-muted disabled:opacity-30 transition-colors"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
