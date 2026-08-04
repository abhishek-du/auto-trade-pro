// Extracted from pages/Trades.jsx (2026-08-04 redesign) -- see utils/tradeFormat.js header comment.
export default function DirectionBadge({ direction }) {
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
