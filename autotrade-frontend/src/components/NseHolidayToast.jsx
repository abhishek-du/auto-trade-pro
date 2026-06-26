import { useState, useEffect } from 'react'
import { CalendarOff, X } from 'lucide-react'
import { useNseHoliday } from '../hooks/useNseHoliday'

export default function NseHolidayToast() {
  const { holidayName, visible, dismiss } = useNseHoliday()
  const [show, setShow] = useState(false)

  // Two-frame animation: mount first (opacity 0), then animate in on next frame
  useEffect(() => {
    if (visible) {
      const id = requestAnimationFrame(() => setShow(true))
      return () => cancelAnimationFrame(id)
    } else {
      setShow(false)
    }
  }, [visible])

  if (!visible) return null

  return (
    <div
      style={{
        position: 'fixed',
        bottom: '5.5rem',
        right: '1rem',
        zIndex: 9999,
        maxWidth: '22rem',
        width: 'calc(100vw - 2rem)',
        transform: show ? 'translateY(0)' : 'translateY(20px)',
        opacity: show ? 1 : 0,
        transition: 'transform 0.3s ease, opacity 0.3s ease',
        pointerEvents: 'auto',
      }}
      role="alert"
    >
      <div
        style={{
          background: 'linear-gradient(135deg, rgba(30,20,5,0.98) 0%, rgba(20,14,3,0.98) 100%)',
          border: '1px solid rgba(245,158,11,0.55)',
          borderRadius: '0.875rem',
          padding: '0.875rem 1rem',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(245,158,11,0.15)',
          backdropFilter: 'blur(12px)',
          display: 'flex',
          gap: '0.75rem',
          alignItems: 'flex-start',
        }}
      >
        {/* Icon */}
        <div
          style={{
            flexShrink: 0,
            width: 36,
            height: 36,
            borderRadius: '50%',
            background: 'rgba(245,158,11,0.15)',
            border: '1px solid rgba(245,158,11,0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <CalendarOff size={16} style={{ color: '#F59E0B' }} />
        </div>

        {/* Content */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', marginBottom: '0.25rem' }}>
            <span
              style={{
                fontSize: '0.65rem',
                fontWeight: 700,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: '#F59E0B',
              }}
            >
              NSE Market Holiday
            </span>
            <span
              style={{
                fontSize: '0.6rem',
                background: 'rgba(245,158,11,0.15)',
                color: '#FCD34D',
                border: '1px solid rgba(245,158,11,0.3)',
                borderRadius: 4,
                padding: '1px 5px',
                fontWeight: 600,
              }}
            >
              CLOSED
            </span>
          </div>
          <p style={{ fontSize: '0.82rem', color: '#F1F5F9', fontWeight: 600, margin: 0, lineHeight: 1.3 }}>
            {holidayName}
          </p>
          <p style={{ fontSize: '0.72rem', color: '#94A3B8', margin: '0.2rem 0 0', lineHeight: 1.4 }}>
            No new trades today. Markets reopen on the next trading day.
          </p>
        </div>

        {/* Dismiss */}
        <button
          onClick={dismiss}
          title="Dismiss — won't show again today"
          style={{
            flexShrink: 0,
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: '#64748B',
            padding: '2px',
            borderRadius: 4,
            display: 'flex',
            alignItems: 'center',
            transition: 'color 0.15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.color = '#CBD5E1')}
          onMouseLeave={e => (e.currentTarget.style.color = '#64748B')}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
