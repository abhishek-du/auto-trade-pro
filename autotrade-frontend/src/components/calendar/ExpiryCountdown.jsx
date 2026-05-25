import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

function urgencyClass(days) {
  if (days === 0) return 'text-red-400 border-red-500/40 bg-red-500/10'
  if (days <= 3)  return 'text-amber-400 border-amber-500/40 bg-amber-500/8'
  if (days <= 7)  return 'text-yellow-500/80 border-yellow-500/30 bg-yellow-500/5'
  return 'text-muted border-border bg-transparent'
}

export default function ExpiryCountdown() {
  const [data, setData] = useState(null)
  const navigate = useNavigate()

  useEffect(() => {
    const load = () =>
      fetch('/api/v1/india/calendar/upcoming?days=14')
        .then(r => r.json())
        .then(d => setData(d))
        .catch(() => {})
    load()
    const id = setInterval(load, 300_000)
    return () => clearInterval(id)
  }, [])

  if (!data) return null

  const expiry   = data.next_expiry
  const rbi      = data.next_rbi

  if (!expiry && !rbi) return null

  return (
    <div className="flex items-center gap-1.5">
      {expiry && (
        <button
          onClick={() => navigate('/calendar')}
          className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold border transition-all ${urgencyClass(expiry.days_away)}`}
          title={expiry.title}
        >
          <span className="hidden sm:inline">Expiry:</span>
          <span>{expiry.days_away === 0 ? 'TODAY' : expiry.days_away === 1 ? 'TMR' : `${expiry.days_away}d`}</span>
        </button>
      )}
      {rbi && (
        <button
          onClick={() => navigate('/calendar')}
          className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold border transition-all ${urgencyClass(rbi.days_away)}`}
          title={rbi.title}
        >
          <span className="hidden sm:inline">RBI:</span>
          <span>{rbi.days_away === 0 ? 'TODAY' : rbi.days_away === 1 ? 'TMR' : `${rbi.days_away}d`}</span>
        </button>
      )}
    </div>
  )
}
