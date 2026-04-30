export default function CandlestickChart({ symbol, data }) {
  return (
    <div className="bg-panel border border-border rounded-xl p-4 h-80 flex items-center justify-center">
      <span className="text-muted text-sm">CandlestickChart — {symbol ?? 'loading…'}</span>
    </div>
  );
}
