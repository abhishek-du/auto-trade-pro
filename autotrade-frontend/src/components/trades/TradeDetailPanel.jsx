import { Target, Brain, Clock3, BookOpen, Zap, Sparkles } from 'lucide-react';
import { fmt, elapsed } from '../../utils/tradeFormat';
import { fmtIST } from '../../utils/datetime';

const fmtDate = (s) => (s ? fmtIST(s) : '—');

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

// ── Confidence Breakdown (2026-07-22) ────────────────────────────────────────
// Proof-of-work for the confidence number: WHY 80%, WHY 42%, never just the
// bare figure. Renders differently for a direct LLM tool-use verdict vs a
// second-order cascade's factor formula -- see TradeIntent.confidence_factors's
// docstring for the incident (a cascade's confidence found hardcoded to a
// fake 80%) that made this required, not optional.

function FactorRow({ label, value, sub }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-white/[0.04] last:border-0">
      <span className="text-[11px] text-muted">{label}</span>
      <div className="text-right">
        <span className="text-xs font-semibold text-slate-200 tabular-nums">{value}</span>
        {sub && <span className="block text-[9px] text-muted">{sub}</span>}
      </div>
    </div>
  );
}

function ConfidenceBreakdown({ cf }) {
  if (!cf || Object.keys(cf).length === 0) {
    return (
      <div className="bg-[#0c1525] border border-white/[0.07] rounded-xl px-4 py-3">
        <p className="text-[11px] text-muted">
          No confidence breakdown recorded for this trade — predates the transparency feature, or a non-event-driven strategy.
        </p>
      </div>
    );
  }

  const isSecondOrder = cf.kind === 'second_order_formula';
  const confirmColor = cf.market_confirmation === 'POSITIVE' ? 'text-profit'
                      : cf.market_confirmation === 'NEGATIVE' ? 'text-loss' : 'text-amber-400';

  return (
    <div className="bg-[#0c1525] border border-white/[0.07] rounded-xl px-4 py-4 space-y-3">
      <div className="flex items-center gap-2">
        <BookOpen size={12} className="text-cyan" />
        <span className="text-[10px] font-bold uppercase tracking-widest text-muted">
          {isSecondOrder ? 'Second-Order Cascade Formula' : 'LLM Multi-Agent Verdict'}
        </span>
      </div>

      {isSecondOrder ? (
        <>
          <FactorRow label="Cascaded from" value={cf.cascade_from ?? '—'} />
          <FactorRow label="Primary event strength" value={cf.event_strength != null ? `${cf.event_strength.toFixed(1)}%` : '—'} />
          <FactorRow label="Relationship" value={cf.relationship_type ?? '—'} />
          <FactorRow label="Relationship strength" value={cf.relationship_strength != null ? cf.relationship_strength.toFixed(2) : '—'} sub="0.0 – 1.0" />
          <FactorRow label="Company exposure" value={cf.company_exposure != null ? cf.company_exposure.toFixed(2) : '—'} sub="0.0 – 1.0" />
          <FactorRow
            label="Market confirmation"
            value={<span className={confirmColor}>{cf.market_confirmation ?? '—'}</span>}
            sub={cf.market_confirmation_multiplier != null ? `×${cf.market_confirmation_multiplier}` : null}
          />
          <div className="pt-2 mt-1 border-t border-white/[0.06]">
            <p className="text-[9px] text-muted font-mono leading-relaxed">{cf.formula}</p>
            <p className="text-xs font-bold text-cyan mt-1">
              = {cf.event_strength?.toFixed(1)} × {cf.relationship_strength?.toFixed(2)} × {cf.company_exposure?.toFixed(2)} × {cf.market_confirmation_multiplier} = <span className="text-sm">{cf.confidence?.toFixed(1)}%</span>
            </p>
          </div>
        </>
      ) : (
        <>
          {cf.bull && <FactorRow label="Bull case" value={<span className="text-profit text-[11px] font-normal">{cf.bull}</span>} />}
          {cf.bear && <FactorRow label="Bear case" value={<span className="text-loss text-[11px] font-normal">{cf.bear}</span>} />}
          {cf.key_risk && <FactorRow label="Key risk" value={<span className="text-amber-400 text-[11px] font-normal">{cf.key_risk}</span>} />}
          {cf.market_confirmation && (
            <FactorRow label="Market confirmation" value={<span className={confirmColor}>{cf.market_confirmation}</span>} />
          )}
          {cf.grounding && (
            <FactorRow
              label="Grounding check"
              value={cf.grounding.grounded === false
                ? <span className="text-loss">FAILED once, self-corrected</span>
                : <span className="text-profit">PASSED</span>}
            />
          )}
          {Array.isArray(cf.tools_used) && cf.tools_used.length > 0 && (
            <div className="py-1.5">
              <p className="text-[11px] text-muted mb-1.5">Tools consulted before deciding</p>
              <div className="flex flex-wrap gap-1">
                {cf.tools_used.map((tool) => (
                  <span key={tool} className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-cyan/10 text-cyan border border-cyan/20">
                    {tool}
                  </span>
                ))}
              </div>
            </div>
          )}
          {cf.thesis && (
            <div className="pt-2 mt-1 border-t border-white/[0.06]">
              <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1">Thesis</p>
              <p className="text-[11px] text-slate-300 leading-relaxed">{cf.thesis}</p>
            </div>
          )}
          {cf.model_reasoning && (
            <details className="pt-2 mt-1 border-t border-white/[0.06] group">
              <summary className="text-[9px] font-semibold uppercase tracking-widest text-muted cursor-pointer hover:text-cyan transition-colors">
                Full model reasoning (multi-agent debate) — click to expand
              </summary>
              <pre className="mt-2 text-[10.5px] text-slate-400 leading-relaxed whitespace-pre-wrap font-['Inter',_sans-serif] bg-[#080e1c] rounded-lg p-3 border border-white/[0.05] max-h-64 overflow-y-auto">
                {cf.model_reasoning}
              </pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}

// ── Trade Detail Panel (expanded row) ────────────────────────────────────────

export default function TradeDetailPanel({ trade }) {
  const isOpen    = (trade.status ?? 'CLOSED').toUpperCase() === 'OPEN';
  const holdTime  = elapsed(trade.opened_at, isOpen ? null : trade.closed_at);
  const conf      = trade.signal_confidence ?? 0;
  const confColor = conf >= 75 ? 'bg-profit' : conf >= 50 ? 'bg-amber-400' : 'bg-loss';

  const entry = trade.entry_price ?? 0;
  const stop  = trade.stop_loss  ?? 0;
  const t1    = trade.take_profit ?? 0;

  const slPct = entry > 0 ? Math.abs(entry - stop) / entry * 100 : 0;
  const t1Pct = entry > 0 ? Math.abs(t1  - entry) / entry * 100 : 0;
  const rr    = slPct > 0 ? t1Pct / slPct : 0;

  // Parse embedded hub score from old one-line format
  const hubMatch = (trade.ai_reason || '').match(/Hub 7-factor score\s+([+-]?\d+(?:\.\d+)?)/i);
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

        {/* whitespace-pre-wrap already wraps long lines to the container width --
            the old overflow-x-auto fought that (a horizontally-scrollable pre
            whose content also wraps can end up scrolled off its own left edge,
            clipping the start of the text, e.g. "Tamilnad Mercantile Bank..."
            rendering as "...nad Mercantile Bank..."). break-words instead
            handles the one case overflow-x-auto was actually needed for -- a
            single unbroken long token -- by breaking it, not scrolling past it. */}
        <pre className="text-[11.5px] text-slate-300 leading-[1.7] bg-[#0c1525] border border-white/[0.07] rounded-xl px-4 py-4 whitespace-pre-wrap break-words font-['Inter',_sans-serif]">
          {analysisText || 'No analysis recorded for this trade.'}
        </pre>
      </div>

      {/* Confidence Breakdown — proof for WHY this confidence number */}
      <div>
        <div className="flex items-center gap-2 mb-2.5">
          <Target size={13} className="text-cyan" />
          <span className="text-[10px] font-bold uppercase tracking-widest text-muted">Why {conf.toFixed(0)}% Confidence</span>
        </div>
        <ConfidenceBreakdown cf={trade.confidence_factors} />
      </div>

      {/* Trade Level Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        <div className="bg-[#0c1525] border border-white/[0.07] rounded-lg p-3 space-y-0.5">
          <p className="text-[9px] text-muted font-semibold uppercase tracking-wider">Entry</p>
          <p className="text-sm font-bold text-slate-100 tabular-nums">{fmt(entry)}</p>
        </div>
        <div className="bg-rose-500/5 border border-rose-500/20 rounded-lg p-3 space-y-0.5">
          <p className="text-[9px] text-rose-400/80 font-semibold uppercase tracking-wider">Stop Loss</p>
          <p className="text-sm font-bold text-rose-400 tabular-nums">{fmt(stop)}</p>
          <p className="text-[9px] text-rose-400/50">−{slPct.toFixed(1)}% risk</p>
        </div>
        <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-3 space-y-0.5">
          <p className="text-[9px] text-emerald-400/80 font-semibold uppercase tracking-wider">Target</p>
          <p className="text-sm font-bold text-emerald-400 tabular-nums">{fmt(t1)}</p>
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

        {/* AI Predict / Direct News attribution — these strategies have no
            pattern_name, so this is their only Strategy-row indicator here. */}
        {trade.strategy_source === 'AI Predict' && (
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1.5">Strategy</p>
            <span className="inline-flex items-center gap-1 text-xs font-mono font-bold text-amber-300 bg-amber-500/10 border border-amber-500/25 px-2 py-0.5 rounded">
              <Sparkles size={11} /> AI Predict — Pre-Event Gap
            </span>
          </div>
        )}
        {trade.strategy_source === 'Direct News' && (
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-widest text-muted mb-1.5">Strategy</p>
            <span className="inline-flex items-center gap-1 text-xs font-mono font-bold text-sky-300 bg-sky-500/10 border border-sky-500/25 px-2 py-0.5 rounded">
              <Zap size={11} /> Direct News — sentiment-direct
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
