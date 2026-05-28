import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

const COLOR_MAP = {
  blue:   { dot: 'bg-blue-400',    header: 'text-blue-400',    badge: 'bg-blue-500/15 text-blue-400 border-blue-500/30' },
  green:  { dot: 'bg-emerald-400', header: 'text-emerald-400', badge: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
  red:    { dot: 'bg-red-400',     header: 'text-red-400',     badge: 'bg-red-500/15 text-red-400 border-red-500/30' },
  purple: { dot: 'bg-purple-400',  header: 'text-purple-400',  badge: 'bg-purple-500/15 text-purple-400 border-purple-500/30' },
  teal:   { dot: 'bg-teal-400',    header: 'text-teal-400',    badge: 'bg-teal-500/15 text-teal-400 border-teal-500/30' },
  amber:  { dot: 'bg-amber-400',   header: 'text-amber-400',   badge: 'bg-amber-500/15 text-amber-400 border-amber-500/30' },
}

function highlightNumbers(text) {
  if (!text) return text
  // Bold numbers with % or ₹
  return text
    .replace(/₹[\d,\.]+(\s*(?:Cr|L|K|bn|mn))?/g, m => `<span class="text-cyan font-semibold">${m}</span>`)
    .replace(/(\d+\.?\d*)\s*%/g, (m, n) => {
      const num = parseFloat(n)
      const cls = num > 0 ? 'text-emerald-400 font-semibold' : num < 0 ? 'text-red-400 font-semibold' : 'text-slate-300 font-semibold'
      return `<span class="${cls}">${m}</span>`
    })
}

export default function SummarySection({ title, emoji, items = [], color = 'blue', maxVisible = 3 }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = COLOR_MAP[color] || COLOR_MAP.blue

  if (!items?.length) return null

  const visible = expanded ? items : items.slice(0, maxVisible)
  const remaining = items.length - maxVisible

  return (
    <div className="rounded-xl border border-border" style={{ background: '#0F1829' }}>
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        {emoji && <span className="text-sm">{emoji}</span>}
        <h3 className={`font-semibold text-sm ${cfg.header}`}>{title}</h3>
        <span className={`ml-auto text-[9px] font-bold px-1.5 py-0.5 rounded-full border ${cfg.badge}`}>
          {items.length}
        </span>
      </div>
      <div className="px-4 py-3 space-y-2.5">
        {visible.map((item, i) => (
          <div key={i} className="flex items-start gap-2.5">
            <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot} mt-1.5 shrink-0`} />
            <p
              className="text-slate-300 text-xs leading-relaxed flex-1"
              dangerouslySetInnerHTML={{ __html: highlightNumbers(item) }}
            />
          </div>
        ))}
        {remaining > 0 && !expanded && (
          <button
            onClick={() => setExpanded(true)}
            className={`flex items-center gap-1 text-xs font-medium mt-1 ${cfg.header} hover:opacity-80`}
          >
            <ChevronDown size={12} /> Show {remaining} more
          </button>
        )}
        {expanded && items.length > maxVisible && (
          <button
            onClick={() => setExpanded(false)}
            className={`flex items-center gap-1 text-xs font-medium mt-1 ${cfg.header} hover:opacity-80`}
          >
            <ChevronUp size={12} /> Show less
          </button>
        )}
      </div>
    </div>
  )
}
