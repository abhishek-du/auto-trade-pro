import { useState, useRef, useEffect } from 'react'
import { ChevronDown, Plus, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'

export default function PortfolioSelector({ portfolios, activeId, onSelect, onCreate, onDelete }) {
  const [open, setOpen]     = useState(false)
  const [adding, setAdding] = useState(false)
  const [name, setName]     = useState('')
  const [creating, setCreating] = useState(false)
  const containerRef = useRef(null)

  const active = portfolios.find(p => p.id === activeId)

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!open) return
    function onOutside(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false)
        setAdding(false)
      }
    }
    document.addEventListener('mousedown', onOutside)
    return () => document.removeEventListener('mousedown', onOutside)
  }, [open])

  async function handleCreate(e) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setCreating(true)
    try {
      await onCreate(trimmed)
      setName('')
      setAdding(false)
      setOpen(false)
      toast.success(`Portfolio "${trimmed}" created`)
    } catch (err) {
      toast.error(err.message || 'Failed to create portfolio')
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(e, id, pName) {
    e.stopPropagation()
    if (!confirm(`Delete portfolio "${pName}"? This removes all holdings and transactions.`)) return
    try {
      await onDelete(id)
      setOpen(false)
      toast.success('Portfolio deleted')
    } catch {
      toast.error('Failed to delete')
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border glass-panel text-slate-200 text-sm font-medium hover:border-accent/40 transition-colors"
      >
        <span className="max-w-[180px] truncate">{active?.name || 'Select Portfolio'}</span>
        <ChevronDown size={14} className={`text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute top-full mt-1 left-0 z-50 w-64 glass-panel border border-border rounded-xl shadow-xl overflow-hidden">
          {portfolios.map(p => (
            <div
              key={p.id}
              onClick={() => { onSelect(p.id); setOpen(false) }}
              className={`flex items-center justify-between px-4 py-2.5 cursor-pointer transition-colors ${
                p.id === activeId ? 'bg-accent/10 text-white' : 'text-slate-300 hover:bg-white/5'
              }`}
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{p.name}</p>
                {p.summary?.holdings_count > 0 && (
                  <p className="text-[10px] text-muted">{p.summary.holdings_count} stocks</p>
                )}
              </div>
              <button
                onClick={(e) => handleDelete(e, p.id, p.name)}
                className="ml-2 p-1 rounded text-muted hover:text-loss hover:bg-loss/10 transition-colors shrink-0"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}

          <div className="border-t border-border px-3 py-2">
            {adding ? (
              <form onSubmit={handleCreate} className="flex gap-1.5">
                <input
                  autoFocus
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder="Portfolio name…"
                  className="flex-1 bg-bg border border-border rounded px-2 py-1 text-xs text-slate-200 outline-none focus:border-accent/50"
                />
                <button type="submit" disabled={creating || !name.trim()} className="px-2 py-1 rounded bg-accent/20 text-accent text-xs font-semibold disabled:opacity-50">{creating ? '…' : 'Add'}</button>
                <button type="button" onClick={() => setAdding(false)} className="px-2 py-1 rounded text-muted text-xs">✕</button>
              </form>
            ) : (
              <button
                onClick={() => setAdding(true)}
                className="flex items-center gap-1.5 w-full px-1 py-1.5 text-xs text-accent hover:text-cyan transition-colors"
              >
                <Plus size={12} /> New Portfolio
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
