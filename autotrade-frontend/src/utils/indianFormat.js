export function formatINR(num, decimals = 2) {
  if (num === null || num === undefined || isNaN(num)) return '—'
  const abs  = Math.abs(num)
  const sign = num < 0 ? '-' : ''
  if (abs >= 10_000_000) return sign + '₹' + (abs / 10_000_000).toFixed(decimals) + ' Cr'
  if (abs >= 100_000)    return sign + '₹' + (abs / 100_000).toFixed(decimals) + ' L'
  if (abs >= 1_000)
    return sign + '₹' + abs.toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
  return sign + '₹' + abs.toFixed(decimals)
}

export function formatVolume(vol) {
  if (!vol && vol !== 0) return '—'
  if (vol >= 10_000_000) return (vol / 10_000_000).toFixed(1) + 'M'
  if (vol >= 1_000_000)  return (vol / 1_000_000).toFixed(1) + 'M'
  if (vol >= 1_000)      return (vol / 1_000).toFixed(0) + 'K'
  return String(vol)
}

export function formatChange(val, prefix = '') {
  if (val === null || val === undefined) return '—'
  const sign = val >= 0 ? '+' : ''
  return sign + prefix + val.toFixed(2)
}

export function formatPct(val) {
  if (val === null || val === undefined) return '—'
  const sign = val >= 0 ? '+' : ''
  return sign + val.toFixed(2) + '%'
}

export function timeSince(dateStr) {
  if (!dateStr) return ''
  const diff = (Date.now() - new Date(dateStr)) / 1000
  if (diff < 60)   return Math.floor(diff) + 's ago'
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago'
  return Math.floor(diff / 3600) + 'h ago'
}
