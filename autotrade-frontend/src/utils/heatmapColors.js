export function getHeatmapColor(changePct) {
  const abs   = Math.abs(changePct || 0)
  const isPos = (changePct || 0) >= 0

  if (abs < 0.3) {
    return { bg: 'rgba(30,41,59,0.7)', text: '#94A3B8', border: '#334155' }
  }

  if (isPos) {
    if (abs >= 3)  return { bg: '#14532D', text: '#BBF7D0', border: '#166534' }
    if (abs >= 2)  return { bg: '#166534', text: '#D1FAE5', border: '#15803D' }
    if (abs >= 1)  return { bg: '#15803D', text: '#ECFDF5', border: '#16A34A' }
    return              { bg: 'rgba(21,128,61,0.35)', text: '#86EFAC', border: '#166534' }
  } else {
    if (abs >= 3)  return { bg: '#7F1D1D', text: '#FEE2E2', border: '#991B1B' }
    if (abs >= 2)  return { bg: '#991B1B', text: '#FEE2E2', border: '#B91C1C' }
    if (abs >= 1)  return { bg: '#B91C1C', text: '#FEF2F2', border: '#DC2626' }
    return              { bg: 'rgba(185,28,28,0.35)', text: '#FCA5A5', border: '#991B1B' }
  }
}

export function getChangePctLabel(changePct) {
  if (changePct == null) return '—'
  const sign = changePct >= 0 ? '+' : ''
  return sign + changePct.toFixed(2) + '%'
}

export const SECTOR_COLORS = {
  IT:       { accent: '#3B82F6', light: 'rgba(59,130,246,0.15)'  },
  Banking:  { accent: '#8B5CF6', light: 'rgba(139,92,246,0.15)'  },
  Pharma:   { accent: '#10B981', light: 'rgba(16,185,129,0.15)'  },
  Auto:     { accent: '#F59E0B', light: 'rgba(245,158,11,0.15)'  },
  FMCG:     { accent: '#14B8A6', light: 'rgba(20,184,166,0.15)'  },
  Metals:   { accent: '#F97316', light: 'rgba(249,115,22,0.15)'  },
  Energy:   { accent: '#EF4444', light: 'rgba(239,68,68,0.15)'   },
  Infra:    { accent: '#EC4899', light: 'rgba(236,72,153,0.15)'  },
  Consumer: { accent: '#6366F1', light: 'rgba(99,102,241,0.15)'  },
  Telecom:  { accent: '#0EA5E9', light: 'rgba(14,165,233,0.15)'  },
}

export const HEATMAP_LEGEND = [
  { label: '> +3%',    changePct:  3.5 },
  { label: '+2–3%',    changePct:  2.5 },
  { label: '+1–2%',    changePct:  1.5 },
  { label: '0–+1%',    changePct:  0.5 },
  { label: 'Flat',     changePct:  0   },
  { label: '0–-1%',    changePct: -0.5 },
  { label: '-1–-2%',   changePct: -1.5 },
  { label: '< -3%',    changePct: -3.5 },
]
