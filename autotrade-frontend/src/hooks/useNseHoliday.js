import { useState, useEffect } from 'react'
import { apiFetch } from '../api/client'

export function useNseHoliday() {
  const today = new Date().toISOString().slice(0, 10)
  const dismissKey = `nse_holiday_dismissed_${today}`

  const [holidayName, setHolidayName] = useState(null)
  const [dismissed, setDismissed] = useState(false)
  const [ready, setReady] = useState(false)

  // Read dismissed state from localStorage after mount (avoids SSR issues)
  useEffect(() => {
    if (localStorage.getItem(dismissKey) === '1') {
      setDismissed(true)
    }
  }, [dismissKey])

  useEffect(() => {
    apiFetch('/api/v1/india/market-status')
      .then(data => {
        setHolidayName(data.today_holiday ? (data.holiday_name || 'NSE Holiday') : null)
        setReady(true)
      })
      .catch(() => setReady(true))
  }, [])

  function dismiss() {
    localStorage.setItem(dismissKey, '1')
    setDismissed(true)
  }

  const visible = ready && holidayName !== null && !dismissed

  return { holidayName, visible, dismiss }
}
