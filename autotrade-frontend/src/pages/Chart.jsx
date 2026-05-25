import { useSearchParams } from 'react-router-dom'
import CandlestickChart from '../components/chart/CandlestickChart'

export default function Chart() {
  const [params] = useSearchParams()
  const symbol   = params.get('symbol') || '^NSEI'
  const name     = params.get('name')   || 'NIFTY 50'

  return (
    <div className="-m-6 h-[calc(100vh-64px)]">
      <CandlestickChart
        symbol={symbol}
        name={name}
        height={window.innerHeight - 64}
        defaultTimeframe="1h"
        showIndicators={true}
        showVolume={true}
        embedded={false}
      />
    </div>
  )
}
