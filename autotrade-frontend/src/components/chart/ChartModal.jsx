import { useEffect } from 'react'
import CandlestickChart from './CandlestickChart'

export default function ChartModal({ symbol, name, isOpen, onClose }) {
  useEffect(() => {
    if (!isOpen) return
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

  if (!isOpen || !symbol) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(5,8,18,0.88)', backdropFilter: 'blur(6px)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-[92vw] rounded-2xl overflow-hidden shadow-2xl border border-border/60"
        style={{ height: '88vh', background: '#080e1c' }}
        onClick={(e) => e.stopPropagation()}
      >
        <CandlestickChart
          symbol={symbol}
          name={name}
          height={window.innerHeight * 0.88}
          defaultTimeframe="1h"
          showIndicators={true}
          showVolume={true}
          embedded={false}
          onClose={onClose}
        />
      </div>
    </div>
  )
}
