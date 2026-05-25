import { useMemo } from 'react'

const ZONES = [
  { min: 0,  max: 2,  color: '#3B82F6', label: 'V. Conservative' },
  { min: 2,  max: 3,  color: '#10B981', label: 'Conservative'    },
  { min: 3,  max: 4,  color: '#06B6D4', label: 'Moderate'        },
  { min: 4,  max: 6,  color: '#F59E0B', label: 'Mod. Aggressive'  },
  { min: 6,  max: 7,  color: '#F97316', label: 'Aggressive'       },
  { min: 7,  max: 10, color: '#EF4444', label: 'V. Aggressive'    },
]

function polarToCartesian(cx, cy, r, angleDeg) {
  const rad = ((angleDeg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

function describeArc(cx, cy, r, startDeg, endDeg) {
  const s   = polarToCartesian(cx, cy, r, startDeg)
  const e   = polarToCartesian(cx, cy, r, endDeg)
  const lg  = endDeg - startDeg > 180 ? 1 : 0
  return `M ${s.x} ${s.y} A ${r} ${r} 0 ${lg} 1 ${e.x} ${e.y}`
}

export default function RiskScoreGauge({ riskScore }) {
  const score  = riskScore?.score  ?? 0
  const label  = riskScore?.label  ?? 'Unknown'
  const color  = riskScore?.color  ?? '#64748B'

  const cx = 80, cy = 80, r = 60
  const startAngle = -180, endAngle = 0    // semicircle

  const zoneArcs = useMemo(() => {
    return ZONES.map(z => {
      const s = startAngle + (z.min / 10) * 180
      const e = startAngle + (z.max / 10) * 180
      return { ...z, path: describeArc(cx, cy, r, s, e) }
    })
  }, [])

  const needleAngle = startAngle + (Math.min(score, 10) / 10) * 180
  const needleTip   = polarToCartesian(cx, cy, r - 6, needleAngle)

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={160} height={90} viewBox="0 0 160 90">
        {/* Zone arcs */}
        {zoneArcs.map(z => (
          <path key={z.label} d={z.path} fill="none" stroke={z.color} strokeWidth={10} strokeLinecap="butt" />
        ))}
        {/* Needle */}
        <line
          x1={cx} y1={cy}
          x2={needleTip.x} y2={needleTip.y}
          stroke="white" strokeWidth={2} strokeLinecap="round"
        />
        <circle cx={cx} cy={cy} r={5} fill="white" />
        {/* Score text */}
        <text x={cx} y={cy - 12} textAnchor="middle" fill="white" fontSize={18} fontWeight="bold">
          {score.toFixed(1)}
        </text>
      </svg>
      <div className="text-xs font-semibold" style={{ color }}>{label}</div>
    </div>
  )
}
