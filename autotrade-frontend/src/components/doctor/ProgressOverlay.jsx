export default function ProgressOverlay({ loading, progress }) {
  if (!loading) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(8,13,26,0.85)', backdropFilter: 'blur(6px)' }}>
      <div className="rounded-2xl border border-border p-8 max-w-sm w-full mx-4 text-center space-y-5" style={{ background: '#0F1829' }}>
        {/* Pulsing cross */}
        <div className="flex justify-center">
          <div className="relative">
            <div className="w-14 h-14 rounded-full flex items-center justify-center" style={{ background: 'rgba(239,68,68,0.15)', animation: 'pulse 2s infinite' }}>
              <span className="text-red-400 text-2xl font-bold">✚</span>
            </div>
            <div className="absolute -inset-2 rounded-full border border-red-500/20 animate-ping" />
          </div>
        </div>

        <div>
          <p className="text-slate-100 font-bold text-base">Diagnosing Portfolio…</p>
          <p className="text-muted text-xs mt-1">Usually takes 20–30 seconds</p>
        </div>

        <div className="rounded-lg border border-border px-4 py-3" style={{ background: '#131E30' }}>
          <p className="text-cyan text-xs font-medium">{progress || 'Initialising…'}</p>
        </div>

        {/* Dots */}
        <div className="flex justify-center gap-1.5">
          {[0, 1, 2].map(i => (
            <div
              key={i}
              className="w-2 h-2 rounded-full bg-cyan"
              style={{ animation: `bounce 1.2s ${i * 0.2}s infinite` }}
            />
          ))}
        </div>

        <p className="text-muted/50 text-[10px]">
          Analysing fundamentals, tax efficiency, and sector allocation
        </p>
      </div>
    </div>
  )
}
