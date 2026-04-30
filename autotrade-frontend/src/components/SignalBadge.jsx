export default function SignalBadge({ signal }) {
  const colors = {
    BUY:  'bg-profit/20 text-profit border-profit/40',
    SELL: 'bg-loss/20 text-loss border-loss/40',
    HOLD: 'bg-neutral/20 text-neutral border-neutral/40',
  };
  const cls = colors[signal?.action?.toUpperCase()] ?? colors.HOLD;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold border ${cls}`}>
      {signal?.action ?? 'HOLD'}
    </span>
  );
}
