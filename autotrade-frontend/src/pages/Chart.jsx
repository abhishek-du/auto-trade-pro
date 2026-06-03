import { useSearchParams } from 'react-router-dom'
import CandlestickChart from '../components/chart/CandlestickChart'

// The page fills the viewport minus the Navbar (64px). We DO NOT pass a
// pixel `height` prop — that was being evaluated once at mount and never
// updated on resize, plus the wrapper's overflow-hidden was clipping the
// SignalPanel at the bottom (Supertrend / EMA-trend bullets).
// Instead the chart is told `fillParent`, and CandlestickChart renders
// h-full so it tracks this container responsively.

export default function Chart() {
  const [params] = useSearchParams()
  const symbol   = params.get('symbol') || '^NSEI'
  const name     = params.get('name')   || 'NIFTY 50'

  return (
    <div className="-m-6 min-h-[calc(100vh-64px)] flex flex-col">
      <CandlestickChart
        symbol={symbol}
        name={name}
        fillParent
        defaultTimeframe="1h"
        showIndicators={true}
        showVolume={true}
        embedded={false}
      />
    </div>
  )
}
