import { useState, useEffect, useMemo } from 'react'
import { apiFetch } from '../api/client'

export function useCalendar() {
  const now = new Date()
  const [currentMonth, setCurrentMonth] = useState({ year: now.getFullYear(), month: now.getMonth() })
  const [eventsByDate, setEventsByDate] = useState({})
  const [upcomingEvents, setUpcomingEvents] = useState([])
  const [upcomingMeta, setUpcomingMeta] = useState({})
  const [loading, setLoading] = useState(true)
  const [activeFilters, setActiveFilters] = useState({
    IPO: true,
    EARNINGS: true,
    RBI_MPC: true,
    FNO_EXPIRY: true,
    HOLIDAY: true,
    FII_DII_RELEASE: false,
  })
  const [selectedDate, setSelectedDate] = useState(null)
  const [selectedEvents, setSelectedEvents] = useState([])

  useEffect(() => {
    const { year, month } = currentMonth
    setLoading(true)
    apiFetch(`/api/v1/india/calendar/month/${year}/${month + 1}`)
      .then(data => {
        setEventsByDate(data.events_by_date || {})
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [currentMonth])

  useEffect(() => {
    apiFetch('/api/v1/india/calendar/upcoming?days=14')
      .then(data => {
        setUpcomingEvents(data.events || [])
        setUpcomingMeta({
          by_type:       data.by_type       || {},
          next_expiry:   data.next_expiry,
          next_rbi:      data.next_rbi,
          next_ipo:      data.next_ipo,
          next_earnings: data.next_earnings,
        })
      })
      .catch(() => {})
  }, [])

  function prevMonth() {
    setCurrentMonth(prev => {
      const d = new Date(prev.year, prev.month - 1)
      return { year: d.getFullYear(), month: d.getMonth() }
    })
  }

  function nextMonth() {
    setCurrentMonth(prev => {
      const d = new Date(prev.year, prev.month + 1)
      return { year: d.getFullYear(), month: d.getMonth() }
    })
  }

  function goToday() {
    const n = new Date()
    setCurrentMonth({ year: n.getFullYear(), month: n.getMonth() })
  }

  function selectDate(dateStr) {
    setSelectedDate(dateStr)
    setSelectedEvents(eventsByDate[dateStr] || [])
  }

  function toggleFilter(type) {
    setActiveFilters(prev => ({ ...prev, [type]: !prev[type] }))
  }

  const filteredEventsByDate = useMemo(() => {
    const result = {}
    Object.entries(eventsByDate).forEach(([date, events]) => {
      const filtered = events.filter(e => activeFilters[e.event_type] !== false)
      if (filtered.length > 0) result[date] = filtered
    })
    return result
  }, [eventsByDate, activeFilters])

  // Count per type in current month view
  const typeCounts = useMemo(() => {
    const counts = {}
    Object.values(eventsByDate).flat().forEach(e => {
      counts[e.event_type] = (counts[e.event_type] || 0) + 1
    })
    return counts
  }, [eventsByDate])

  return {
    currentMonth, prevMonth, nextMonth, goToday,
    filteredEventsByDate, upcomingEvents, upcomingMeta,
    loading, activeFilters, toggleFilter, typeCounts,
    selectedDate, selectedEvents, selectDate,
  }
}
