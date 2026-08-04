// Extracted from pages/Trades.jsx (2026-08-04 redesign) -- see utils/tradeFormat.js header comment.
//
// Color is never the only signal: an arrow glyph is paired with the color so
// gain/loss is distinguishable without relying on color perception alone.
export default function PnLPct({ value }) {
  const n = Number(value ?? 0);
  const isGain = n >= 0;
  return (
    <span
      className={`tabular-nums text-xs font-semibold px-1.5 py-0.5 rounded ${isGain ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}
      aria-label={`${isGain ? 'Gain' : 'Loss'} ${Math.abs(n).toFixed(2)} percent`}
    >
      {isGain ? '▲' : '▼'} {isGain ? '+' : ''}{n.toFixed(2)}%
    </span>
  );
}
