import { useState, useEffect } from 'react'
import { X, Check, AlertTriangle } from 'lucide-react'
import { ASSET_CLASSES } from '../../hooks/useAllocation'

const CLASSES = ['large_cap', 'mid_cap', 'small_cap', 'debt', 'gold', 'international', 'cash']

const PRESETS = {
  Conservative:      { large_cap: 20, mid_cap: 0,  small_cap: 0,  debt: 60, gold: 15, international: 0, cash: 5 },
  Moderate:          { large_cap: 40, mid_cap: 15, small_cap: 5,  debt: 30, gold: 10, international: 0, cash: 0 },
  'Mod. Aggressive': { large_cap: 45, mid_cap: 20, small_cap: 10, debt: 20, gold: 5,  international: 0, cash: 0 },
  Aggressive:        { large_cap: 35, mid_cap: 30, small_cap: 20, debt: 10, gold: 5,  international: 0, cash: 0 },
}

export default function CustomTargetEditor({ isOpen, currentTarget, onChange, onClose }) {
  const [values, setValues] = useState({})

  useEffect(() => {
    if (currentTarget) setValues({ ...currentTarget })
  }, [currentTarget])

  if (!isOpen) return null

  const total = CLASSES.reduce((s, c) => s + (values[c] || 0), 0)
  const remaining = 100 - total
  const isValid   = Math.abs(remaining) < 0.5

  function set(cls, val) {
    setValues(prev => ({ ...prev, [cls]: Math.max(0, Math.min(100, val)) }))
  }

  function applyPreset(name) {
    setValues({ ...PRESETS[name] })
  }

  function handleApply() {
    if (isValid) onChange?.(values)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.7)' }}>
      <div className="w-full max-w-sm rounded-2xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <p className="text-slate-100 font-semibold text-sm">Custom Target Allocation</p>
          <button onClick={onClose} className="text-muted hover:text-white"><X size={16} /></button>
        </div>

        <div className="p-5 space-y-4">
          {/* Presets */}
          <div className="flex gap-1.5 flex-wrap">
            {Object.keys(PRESETS).map(name => (
              <button key={name} onClick={() => applyPreset(name)}
                className="px-2.5 py-1 rounded-lg border border-border text-muted text-[11px] hover:text-slate-300 hover:border-accent/40 transition-colors">
                {name}
              </button>
            ))}
          </div>

          {/* Sliders */}
          <div className="space-y-3">
            {CLASSES.map(cls => {
              const cfg = ASSET_CLASSES[cls] || {}
              const val = values[cls] || 0
              return (
                <div key={cls} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full" style={{ background: cfg.color }} />
                      <span className="text-slate-300">{cfg.label}</span>
                    </span>
                    <div className="flex items-center gap-1">
                      <input
                        type="number" min="0" max="100" step="1"
                        value={val}
                        onChange={e => set(cls, +e.target.value)}
                        className="bg-surface border border-border rounded px-1.5 py-0.5 text-slate-200 text-xs w-12 text-right focus:outline-none focus:border-accent/60"
                      />
                      <span className="text-muted">%</span>
                    </div>
                  </div>
                  <input
                    type="range" min="0" max="100" step="1"
                    value={val}
                    onChange={e => set(cls, +e.target.value)}
                    className="w-full h-1.5 rounded-full appearance-none bg-surface cursor-pointer"
                    style={{ accentColor: cfg.color }}
                  />
                </div>
              )
            })}
          </div>

          {/* Total indicator */}
          <div className={`flex items-center justify-between px-3 py-2 rounded-lg border text-xs font-semibold ${
            isValid
              ? 'border-profit/30 bg-profit/5 text-profit'
              : 'border-amber-400/30 bg-amber-400/5 text-amber-400'
          }`}>
            <span className="flex items-center gap-1.5">
              {isValid ? <Check size={12} /> : <AlertTriangle size={12} />}
              Total: {total.toFixed(0)}%
            </span>
            <span>{isValid ? 'Ready to apply' : `Remaining: ${remaining > 0 ? '+' : ''}${remaining.toFixed(0)}%`}</span>
          </div>

          <button
            onClick={handleApply}
            disabled={!isValid}
            className="w-full py-2.5 rounded-xl bg-accent text-white text-sm font-semibold hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Apply Custom Target
          </button>
        </div>
      </div>
    </div>
  )
}
