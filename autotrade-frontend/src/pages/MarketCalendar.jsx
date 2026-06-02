import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CalendarDays, List, RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react'
import toast from 'react-hot-toast'
import { useCalendar } from '../hooks/useCalendar'
import CalendarGrid  from '../components/calendar/CalendarGrid'
import EventList     from '../components/calendar/EventList'
import FilterBar     from '../components/calendar/FilterBar'
import ListView      from '../components/calendar/ListView'
import UpcomingEventsWidget from '../components/calendar/UpcomingEventsWidget'
import { getEventConfig, daysAwayLabel } from '../utils/eventTypeConfig'
import { apiFetch } from '../api/client'

const MONTHS = ['January','February','March','April','May','June',
                'July','August','September','October','November','December']

function QuickChip({ label, chip, color, border }) {
  if (!chip) return null
  const { label: dayLabel, cls } = daysAwayLabel(chip.days_away)
  return (
    <div
      className="flex items-center gap-2 px-3 py-2 rounded-xl border text-xs"
      style={{ borderColor: border, background: color }}
    >
      <span className="text-muted font-semibold shrink-0">{label}:</span>
      <span className="text-slate-200 font-semibold truncate">{chip.title.length > 28 ? chip.title.slice(0,27)+'…' : chip.title}</span>
      <span className={`shrink-0 font-bold ${cls}`}>{dayLabel}</span>
    </div>
  )
}

export default function MarketCalendar() {
  const [searchParams] = useSearchParams()
  const [viewMode, setViewMode] = useState('calendar')
  const [showEventPanel, setShowEventPanel] = useState(false)
  const [seeding, setSeeding] = useState(false)

  const {
    currentMonth, prevMonth, nextMonth, goToday,
    filteredEventsByDate, upcomingEvents, upcomingMeta,
    loading, activeFilters, toggleFilter, typeCounts,
    selectedDate, selectedEvents, selectDate,
  } = useCalendar()

  // Handle ?date= param from UpcomingEventsWidget links
  useEffect(() => {
    const d = searchParams.get('date')
    if (d) {
      selectDate(d)
      setShowEventPanel(true)
    }
  }, [])  // eslint-disable-line

  async function handleSeed() {
    setSeeding(true)
    try {
      const r = await apiFetch('/api/v1/india/calendar/seed', { method: 'POST' })
      const d = await r.json()
      toast.success(`Calendar seeded: ${d.total_inserted} events`)
      // Reload current month
      window.location.reload()
    } catch {
      toast.error('Seed failed')
    } finally {
      setSeeding(false)
    }
  }

  function handleDateSelect(dateStr) {
    selectDate(dateStr)
    setShowEventPanel(true)
  }

  const allMonthEvents = Object.values(filteredEventsByDate).flat()

  return (
    <div className="space-y-5 fade-in">

      {/* ── Row 1: Header ── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <CalendarDays size={18} className="text-cyan" />
            Market Calendar
          </h1>
          <p className="text-muted text-sm mt-0.5">Indian market events — NSE, BSE, RBI</p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* View toggle */}
          <div className="flex items-center gap-0.5 bg-panel border border-border rounded-lg p-0.5">
            <button
              title="Calendar view"
              onClick={() => setViewMode('calendar')}
              className={`p-1.5 rounded transition-colors ${viewMode === 'calendar' ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'}`}
            >
              <CalendarDays size={14} />
            </button>
            <button
              title="List view"
              onClick={() => setViewMode('list')}
              className={`p-1.5 rounded transition-colors ${viewMode === 'list' ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'}`}
            >
              <List size={14} />
            </button>
          </div>

          <button
            onClick={handleSeed}
            disabled={seeding}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs font-medium text-slate-300 hover:text-white hover:border-accent/40 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={12} className={seeding ? 'animate-spin' : ''} />
            Refresh Data
          </button>
        </div>
      </div>

      {/* ── Row 2: Quick stats ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <QuickChip label="Next Expiry" chip={upcomingMeta.next_expiry}
          color="rgba(245,158,11,0.08)" border="rgba(245,158,11,0.3)" />
        <QuickChip label="Next RBI" chip={upcomingMeta.next_rbi}
          color="rgba(220,38,38,0.08)" border="rgba(220,38,38,0.3)" />
        <QuickChip label="Next IPO" chip={upcomingMeta.next_ipo}
          color="rgba(139,92,246,0.08)" border="rgba(139,92,246,0.3)" />
        <QuickChip label="Next Results" chip={upcomingMeta.next_earnings}
          color="rgba(13,148,136,0.08)" border="rgba(13,148,136,0.3)" />
      </div>

      {/* ── Row 3: Filter bar ── */}
      <div className="bg-panel border border-border rounded-xl px-4 py-3">
        <FilterBar
          activeFilters={activeFilters}
          onToggle={toggleFilter}
          typeCounts={typeCounts}
        />
      </div>

      {/* ── Main content ── */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">

        {/* Calendar / List — 75% */}
        <div className="lg:col-span-3">
          <div className="bg-panel border border-border rounded-xl p-4">

            {/* Month navigation */}
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <button onClick={prevMonth} className="p-1.5 rounded-lg text-muted hover:text-white hover:bg-white/10 transition-colors">
                  <ChevronLeft size={16} />
                </button>
                <h2 className="text-slate-100 font-bold text-base min-w-[160px] text-center">
                  {MONTHS[currentMonth.month]} {currentMonth.year}
                </h2>
                <button onClick={nextMonth} className="p-1.5 rounded-lg text-muted hover:text-white hover:bg-white/10 transition-colors">
                  <ChevronRight size={16} />
                </button>
                <button onClick={goToday} className="ml-1 px-2.5 py-1 rounded-lg text-xs font-semibold text-muted border border-border hover:text-slate-200 hover:border-accent/40 transition-colors">
                  Today
                </button>
              </div>
              <span className="text-muted text-xs">{allMonthEvents.length} events</span>
            </div>

            {loading ? (
              <div className="grid grid-cols-7 gap-1">
                {Array.from({length: 35}).map((_,i) => (
                  <div key={i} className="h-20 rounded-lg bg-slate-800/50 animate-pulse" />
                ))}
              </div>
            ) : viewMode === 'calendar' ? (
              <CalendarGrid
                year={currentMonth.year}
                month={currentMonth.month}
                eventsByDate={filteredEventsByDate}
                selectedDate={selectedDate}
                onDateSelect={handleDateSelect}
              />
            ) : (
              <ListView eventsByDate={filteredEventsByDate} />
            )}
          </div>

          {/* Legend */}
          <div className="bg-panel border border-border rounded-xl px-4 py-3 mt-3">
            <div className="flex items-center gap-4 flex-wrap">
              <span className="text-muted text-[10px] font-semibold uppercase tracking-wider shrink-0">Legend:</span>
              {['IPO','EARNINGS','RBI_MPC','FNO_EXPIRY','HOLIDAY'].map(type => {
                const cfg = getEventConfig(type)
                return (
                  <div key={type} className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: cfg.color }} />
                    <span className="text-[10px] text-muted">{cfg.label}</span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        {/* Sidebar panel — 25% */}
        <div className="lg:col-span-1">
          {showEventPanel && selectedEvents.length > 0 ? (
            <EventList
              events={selectedEvents}
              date={selectedDate}
              onClose={() => setShowEventPanel(false)}
            />
          ) : (
            <UpcomingEventsWidget events={upcomingEvents} maxItems={10} />
          )}
        </div>
      </div>
    </div>
  )
}
