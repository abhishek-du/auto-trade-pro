export const EVENT_TYPE_CONFIG = {
  IPO: {
    label: 'IPO',
    color: '#8B5CF6',
    bg: 'rgba(139,92,246,0.12)',
    border: 'rgba(139,92,246,0.35)',
    dotClass: 'bg-violet-500',
    importance_order: 1,
  },
  EARNINGS: {
    label: 'Earnings',
    color: '#0D9488',
    bg: 'rgba(13,148,136,0.12)',
    border: 'rgba(13,148,136,0.35)',
    dotClass: 'bg-teal-500',
    importance_order: 2,
  },
  RBI_MPC: {
    label: 'RBI Policy',
    color: '#DC2626',
    bg: 'rgba(220,38,38,0.12)',
    border: 'rgba(220,38,38,0.35)',
    dotClass: 'bg-red-500',
    importance_order: 0,
  },
  FNO_EXPIRY: {
    label: 'F&O Expiry',
    color: '#F59E0B',
    bg: 'rgba(245,158,11,0.12)',
    border: 'rgba(245,158,11,0.35)',
    dotClass: 'bg-amber-500',
    importance_order: 3,
  },
  HOLIDAY: {
    label: 'Holiday',
    color: '#6B7280',
    bg: 'rgba(107,114,128,0.12)',
    border: 'rgba(107,114,128,0.35)',
    dotClass: 'bg-slate-500',
    importance_order: 4,
  },
  FII_DII_RELEASE: {
    label: 'FII/DII Data',
    color: '#2563EB',
    bg: 'rgba(37,99,235,0.12)',
    border: 'rgba(37,99,235,0.35)',
    dotClass: 'bg-blue-500',
    importance_order: 5,
  },
  DIVIDEND: {
    label: 'Dividend',
    color: '#16A34A',
    bg: 'rgba(22,163,74,0.12)',
    border: 'rgba(22,163,74,0.35)',
    dotClass: 'bg-green-500',
    importance_order: 4,
  },
}

export function getEventConfig(eventType) {
  return EVENT_TYPE_CONFIG[eventType] || EVENT_TYPE_CONFIG.HOLIDAY
}

export function daysAwayLabel(daysAway) {
  if (daysAway === 0)  return { label: 'TODAY',    cls: 'text-red-400 font-bold' }
  if (daysAway === 1)  return { label: 'TOMORROW', cls: 'text-amber-400 font-semibold' }
  if (daysAway <= 3)   return { label: `In ${daysAway} days`, cls: 'text-amber-500' }
  if (daysAway <= 7)   return { label: `In ${daysAway} days`, cls: 'text-yellow-500' }
  return { label: `In ${daysAway} days`, cls: 'text-muted' }
}
