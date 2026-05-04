export default function SignalBadge({ signal }) {
  const type = (signal?.signal_type ?? signal?.action ?? 'HOLD').toUpperCase();
  const map = {
    BUY:  { cls: 'bg-profit/12 text-profit border-profit/25', dot: 'bg-profit' },
    SELL: { cls: 'bg-loss/12 text-loss border-loss/25',       dot: 'bg-loss'   },
    HOLD: { cls: 'bg-white/5 text-muted border-border',       dot: 'bg-muted'  },
  };
  const { cls, dot } = map[type] ?? map.HOLD;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-bold border ${cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
      {type}
    </span>
  );
}
