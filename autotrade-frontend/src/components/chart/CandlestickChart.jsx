import { useEffect, useRef, useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  createChart, CandlestickSeries, LineSeries, HistogramSeries,
} from 'lightweight-charts'
import { apiFetch } from '../../api/client'
import {
  X, RefreshCw, TrendingUp, Zap, AlertCircle,
  ChevronDown,
} from 'lucide-react'
import { formatINR, formatVolume } from '../../utils/indianFormat'

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d']
const TF_LABEL   = { '1m': '1M', '5m': '5M', '15m': '15M', '1h': '1H', '1d': '1D' }

const IND_CONFIG = [
  { key: 'ema20',       label: 'EMA 20',      color: '#3B82F6', default: true  },
  { key: 'ema50',       label: 'EMA 50',      color: '#F59E0B', default: true  },
  { key: 'ema200',      label: 'EMA 200',     color: '#8B5CF6', default: false },
  { key: 'supertrend',  label: 'Supertrend',  color: '#10B981', default: true  },
  { key: 'bb',          label: 'BB',          color: '#94A3B8', default: false },
  { key: 'vwap',        label: 'VWAP',        color: '#0D9488', default: true  },
]

function fmtPrice(p) {
  if (p == null) return '—'
  return '₹' + Number(p).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(p) {
  if (p == null) return ''
  const sign = p >= 0 ? '+' : ''
  return `${sign}${p.toFixed(2)}%`
}

// ── Shimmer skeleton ──────────────────────────────────────────────────────────
function ChartSkeleton({ height, symbol, timeframe }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg shimmer"
      style={{ height }}>
      <RefreshCw size={20} className="text-muted animate-spin" />
      <p className="text-muted text-sm">Loading {symbol} · {timeframe.toUpperCase()} candles…</p>
    </div>
  )
}

// ── Error state ───────────────────────────────────────────────────────────────
function ChartError({ symbol, message, onRetry }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg bg-surface/60 border border-border"
      style={{ minHeight: 200 }}>
      <AlertCircle size={22} className="text-loss" />
      <p className="text-slate-300 text-sm font-medium">Could not load chart data for {symbol}</p>
      {message && <p className="text-muted text-xs max-w-xs text-center">{message}</p>}
      <button onClick={onRetry}
        className="px-3 py-1.5 text-xs bg-accent/20 text-accent border border-accent/30 rounded-lg hover:bg-accent/30 transition-colors">
        Retry
      </button>
    </div>
  )
}

// ── OHLCV legend strip ────────────────────────────────────────────────────────
function OHLCVLegend({ data, timeframe }) {
  if (!data) return null
  const { open, high, low, close, volume } = data
  const isUp = close >= open
  const cls  = isUp ? 'text-profit' : 'text-loss'
  return (
    <div className={`flex items-center gap-3 text-[11px] tabular-nums ${cls}`}>
      {[['O', open], ['H', high], ['L', low], ['C', close]].map(([label, val]) => (
        <span key={label} className="flex items-center gap-0.5">
          <span className="text-muted font-normal">{label}</span>
          <span className="font-semibold">{fmtPrice(val)}</span>
        </span>
      ))}
      {volume != null && (
        <span className="flex items-center gap-0.5">
          <span className="text-muted font-normal">V</span>
          <span className="font-semibold">{formatVolume(volume)}</span>
        </span>
      )}
    </div>
  )
}

// ── Signal panel below chart ──────────────────────────────────────────────────
function SignalPanel({ symbol }) {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const sym = symbol.replace(/\^/, '%5E')
    apiFetch(`/api/v1/india/watchlist/${sym}`)
      .then(d => setData(d))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [symbol])

  if (loading) return null
  if (!data) return null

  const sig  = data.signal
  const conf = data.signal_confidence ?? 0
  const tech = data.technical_summary
  const ai   = data.ai_analysis

  if (!sig && !tech) return null

  const sigCls = sig === 'BUY'
    ? 'bg-profit/10 border-profit/30 text-profit'
    : sig === 'SELL'
    ? 'bg-loss/10 border-loss/30 text-loss'
    : 'bg-warn/10 border-warn/30 text-warn'

  const bullets = [
    tech?.rsi        && `RSI ${tech.rsi} — ${tech.rsi_signal?.toLowerCase().replace('_', ' ')}`,
    tech?.macd_signal && `MACD ${tech.macd_signal?.toLowerCase().replace(/_/g, ' ')}`,
    tech?.supertrend  && `Supertrend ${tech.supertrend?.toLowerCase()}`,
    tech?.ema_trend   && `EMA trend: ${tech.ema_trend?.toLowerCase().replace(/_/g, ' ')}`,
  ].filter(Boolean)

  return (
    <div className={`border rounded-xl px-4 py-3 ${sigCls}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Zap size={13} />
          <span className="text-xs font-bold uppercase tracking-wider">
            {sig ? `Signal: ${sig}` : 'Analysis'}
          </span>
          {sig && conf > 0 && (
            <span className="text-[11px] opacity-80">{conf.toFixed(0)}% confidence</span>
          )}
        </div>
      </div>
      {bullets.length > 0 && (
        <ul className="space-y-0.5">
          {bullets.map((b, i) => (
            <li key={i} className="text-xs opacity-80 flex items-center gap-1.5">
              <span className="w-1 h-1 rounded-full bg-current shrink-0" />
              {b}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function AIPredictPanel({ symbol }) {
  const [prediction, setPrediction] = useState('')
  const [loading, setLoading] = useState(false)

  const handlePredict = async () => {
    setLoading(true)
    setPrediction('')
    try {
      const res = await apiFetch(`/api/v1/chat/predict-chart/${symbol}`)
      setPrediction(res.prediction || 'No prediction available.')
    } catch (e) {
      setPrediction('Failed to load prediction.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mt-8 mb-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-5">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500/20 to-accent/5 flex items-center justify-center border border-violet-500/20 shadow-lg shadow-violet-500/10">
            <Zap size={20} className="text-violet-400" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-slate-100 tracking-tight uppercase">AI Candlestick Prediction</h3>
            <p className="text-[10px] text-muted mt-0.5 uppercase tracking-widest">Next Candle Forecast</p>
          </div>
        </div>
        <button 
          onClick={handlePredict} 
          disabled={loading}
          className="relative overflow-hidden px-5 py-2.5 text-xs font-bold text-white rounded-lg shadow-lg shadow-violet-500/20 bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 transition-all disabled:opacity-70 disabled:cursor-not-allowed group w-full sm:w-auto"
        >
          {loading ? (
            <div className="flex items-center justify-center gap-2">
              <RefreshCw size={14} className="animate-spin" />
              <span>Analyzing Price Action...</span>
            </div>
          ) : (
            <div className="flex items-center justify-center gap-2">
              <Zap size={14} className="group-hover:scale-110 transition-transform" />
              <span>Predict Next Candle</span>
            </div>
          )}
          {!loading && <div className="absolute inset-0 bg-white/10 translate-y-full group-hover:translate-y-0 transition-transform duration-300 ease-out pointer-events-none" />}
        </button>
      </div>

      {loading && (
        <div className="rounded-2xl border border-violet-500/20 bg-black/20 p-8 backdrop-blur-sm animate-pulse flex flex-col items-center justify-center gap-5 min-h-[160px] fade-in">
          <div className="flex gap-1.5 typing-indicator">
            <span style={{ backgroundColor: '#A78BFA' }}></span>
            <span style={{ backgroundColor: '#A78BFA' }}></span>
            <span style={{ backgroundColor: '#A78BFA' }}></span>
          </div>
          <span className="text-[10px] font-bold text-violet-300 uppercase tracking-widest">Simulating thousands of patterns...</span>
        </div>
      )}

      {prediction && !loading && (
        <div className="relative rounded-2xl border border-violet-500/20 bg-gradient-to-br from-violet-900/20 to-[#0A1121] p-5 sm:p-8 overflow-hidden shadow-2xl shadow-violet-900/10 fade-in slide-in-right">
          {/* Subtle background glow */}
          <div className="absolute -top-20 -left-20 w-48 h-48 bg-violet-600/10 rounded-full blur-[60px] pointer-events-none" />
          <div className="absolute -bottom-20 -right-20 w-48 h-48 bg-accent/10 rounded-full blur-[60px] pointer-events-none" />
          
          <div className="prose-custom relative z-10 text-sm text-slate-200 leading-relaxed font-sans">
            <ReactMarkdown>{prediction}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function CandlestickChart({
  symbol          = 'RELIANCE.NS',
  name            = '',
  defaultTimeframe = '1h',
  height          = 500,
  showIndicators  = true,
  showVolume      = true,
  embedded        = false,
  onClose         = null,
  // When true, the chart fills its parent (flex-1 + h-full) instead of
  // taking a fixed pixel height. Used by the /chart page so a window
  // resize doesn't strand the chart at its mount-time pixel size and
  // doesn't clip the SignalPanel rows (Supertrend, EMA trend) on
  // shorter viewports.
  fillParent      = false,
}) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const seriesRefs   = useRef({})   // keyed by series name
  const wsRef        = useRef(null)
  const cleanupRef   = useRef(null)

  const [timeframe,      setTimeframe]      = useState(defaultTimeframe)
  const [loading,        setLoading]        = useState(true)
  const [error,          setError]          = useState(null)
  const [candleCount,    setCandleCount]    = useState(0)
  const [ohlcv,          setOhlcv]          = useState(null)   // crosshair or latest
  const [latestOhlcv,    setLatestOhlcv]    = useState(null)
  const [indicators,     setIndicators]     = useState(
    Object.fromEntries(IND_CONFIG.map(c => [c.key, c.default]))
  )
  const [showSignal,     setShowSignal]     = useState(true)
  const [settingsOpen,   setSettingsOpen]   = useState(false)

  // ── Chart init (once) ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: Math.max(200, height - 160),
      layout: {
        background:  { color: '#0A1120' },
        textColor:   '#64748B',
        fontSize:    11,
        fontFamily:  'Inter, -apple-system, sans-serif',
      },
      grid: {
        vertLines: { color: '#0F1E35', style: 1 },
        horzLines: { color: '#0F1E35', style: 1 },
      },
      crosshair: {
        vertLine: { width: 1, color: '#334155', style: 1, labelBackgroundColor: '#1E293B' },
        horzLine: { width: 1, color: '#334155', style: 1, labelBackgroundColor: '#1E293B' },
      },
      rightPriceScale: {
        borderColor: '#0F1E35',
        scaleMargins: { top: 0.08, bottom: showVolume ? 0.28 : 0.08 },
      },
      timeScale: {
        borderColor: '#0F1E35',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time) => {
          const d = new Date((time + 19800) * 1000)   // shift to IST for display
          const tf = timeframe
          if (tf === '1d')
            return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })
          return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false })
        },
      },
      localization: {
        priceFormatter: p =>
          '₹' + Number(p).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      },
    })
    chartRef.current = chart

    // Candlestick series
    const candle = chart.addSeries(CandlestickSeries, {
      upColor:        '#10B981',
      downColor:      '#EF4444',
      borderUpColor:  '#10B981',
      borderDownColor:'#EF4444',
      wickUpColor:    '#10B981',
      wickDownColor:  '#EF4444',
    })
    seriesRefs.current.candle = candle

    // Volume histogram
    if (showVolume) {
      const vol = chart.addSeries(HistogramSeries, {
        priceScaleId: 'vol',
        priceFormat:  { type: 'volume' },
        color:        '#3B82F6',
      })
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
      seriesRefs.current.volume = vol
    }

    // EMA lines
    const ema20 = chart.addSeries(LineSeries, {
      color: '#3B82F6', lineWidth: 1.5,
      priceLineVisible: false, lastValueVisible: false, title: 'EMA20',
    })
    const ema50 = chart.addSeries(LineSeries, {
      color: '#F59E0B', lineWidth: 1.5,
      priceLineVisible: false, lastValueVisible: false, title: 'EMA50',
    })
    const ema200 = chart.addSeries(LineSeries, {
      color: '#8B5CF6', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false, title: 'EMA200',
    })
    ema200.applyOptions({ visible: false })
    seriesRefs.current.ema20  = ema20
    seriesRefs.current.ema50  = ema50
    seriesRefs.current.ema200 = ema200

    // Supertrend line
    const st = chart.addSeries(LineSeries, {
      color: '#10B981', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false, title: 'ST',
    })
    seriesRefs.current.supertrend = st

    // Bollinger Bands
    const bbUp = chart.addSeries(LineSeries, {
      color: '#64748B', lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false, title: 'BB+',
    })
    const bbLo = chart.addSeries(LineSeries, {
      color: '#64748B', lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false, title: 'BB-',
    })
    bbUp.applyOptions({ visible: false })
    bbLo.applyOptions({ visible: false })
    seriesRefs.current.bbUp = bbUp
    seriesRefs.current.bbLo = bbLo

    // VWAP
    const vwap = chart.addSeries(LineSeries, {
      color: '#0D9488', lineWidth: 1.5, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false, title: 'VWAP',
    })
    seriesRefs.current.vwap = vwap

    // Crosshair → legend
    chart.subscribeCrosshairMove(param => {
      if (!param.time) { setOhlcv(null); return }
      const c = param.seriesData?.get(candle)
      if (c) {
        const v = param.seriesData?.get(seriesRefs.current.volume)
        setOhlcv({ ...c, volume: v?.value })
      }
    })

    // Resize observer — must track both width AND height. lightweight-charts
    // does not auto-fit; without applying the new height the canvas keeps
    // its mount-time dimensions and gets clipped when the parent shrinks
    // (e.g. when the SignalPanel renders below it on the /chart page).
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) {
        chart.applyOptions({ width, height })
      }
    })
    ro.observe(containerRef.current)

    cleanupRef.current = () => {
      ro.disconnect()
      chart.remove()
    }
    return () => cleanupRef.current?.()
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── Indicator visibility sync ───────────────────────────────────────────────
  useEffect(() => {
    const s = seriesRefs.current
    s.ema20?.applyOptions({ visible: indicators.ema20 })
    s.ema50?.applyOptions({ visible: indicators.ema50 })
    s.ema200?.applyOptions({ visible: indicators.ema200 })
    s.supertrend?.applyOptions({ visible: indicators.supertrend })
    s.bbUp?.applyOptions({ visible: indicators.bb })
    s.bbLo?.applyOptions({ visible: indicators.bb })
    s.vwap?.applyOptions({ visible: indicators.vwap && timeframe !== '1d' })
  }, [indicators, timeframe])

  // ── Load candle data ────────────────────────────────────────────────────────
  const loadCandles = useCallback(async (tf) => {
    setLoading(true)
    setError(null)
    try {
      // apiFetch already returns parsed JSON and throws on non-2xx, so
      // there's no Response object to call .json()/.ok on.
      const res = await apiFetch(`/api/v1/india/candles/${encodeURIComponent(symbol)}?timeframe=${tf}&limit=500`)
      const { candles, current_price } = res
      if (!candles?.length) { setError('No candle data for this timeframe'); return }

      seriesRefs.current.candle?.setData(candles)
      setCandleCount(candles.length)

      if (seriesRefs.current.volume) {
        seriesRefs.current.volume.setData(
          candles.map(c => ({
            time:  c.time,
            value: c.volume,
            color: c.close >= c.open ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)',
          }))
        )
      }

      const last = candles[candles.length - 1]
      const latest = { ...last, currentPrice: current_price ?? last.close }
      setLatestOhlcv(latest)

      // Fit to last ~120 candles
      chartRef.current?.timeScale().setVisibleLogicalRange({
        from: Math.max(0, candles.length - 120),
        to:   candles.length - 1,
      })

      // Load indicators async (non-blocking)
      if (showIndicators) loadIndicators(tf)

    } catch (err) {
      setError(err.message || 'Failed to load chart data')
    } finally {
      setLoading(false)
    }
  }, [symbol, showIndicators])

  const loadIndicators = useCallback(async (tf) => {
    try {
      // apiFetch already returns parsed JSON.
      const ind = await apiFetch(`/api/v1/india/candles/${encodeURIComponent(symbol)}/indicators?timeframe=${tf}&limit=500`)
      const s   = seriesRefs.current

      if (ind.ema20?.length)  s.ema20?.setData(ind.ema20)
      if (ind.ema50?.length)  s.ema50?.setData(ind.ema50)
      if (ind.ema200?.length) s.ema200?.setData(ind.ema200)

      if (ind.supertrend?.length) {
        s.supertrend?.setData(ind.supertrend.map(d => ({ time: d.time, value: d.value })))
        const lastDir = ind.supertrend[ind.supertrend.length - 1]?.direction
        s.supertrend?.applyOptions({ color: lastDir === 'up' ? '#10B981' : '#EF4444' })
      }

      if (ind.bollinger) {
        s.bbUp?.setData(ind.bollinger.upper)
        s.bbLo?.setData(ind.bollinger.lower)
      }

      if (ind.vwap?.length && tf !== '1d') {
        s.vwap?.setData(ind.vwap)
      }
    } catch { /* indicators are optional */ }
  }, [symbol])

  // ── WebSocket setup ─────────────────────────────────────────────────────────
  // Build the WS URL relative to the page origin so the Vite dev proxy can
  // forward it. The previous hardcoded ws://host:8000 bypassed the proxy,
  // which (a) breaks in any deploy where backend isn't on :8000 and (b) in
  // dev raced the React StrictMode double-mount: the first WS opened and
  // was closed by the cleanup before the backend could send "init", which
  // is what we were seeing as "init failed: WebSocketDisconnect" in the
  // backend logs.
  const setupWS = useCallback((tf) => {
    // Tear down any prior socket without tripping the browser's
    // "closed before the connection is established" warning (StrictMode remount).
    const prev = wsRef.current
    if (prev) {
      if (prev.readyState === WebSocket.OPEN || prev.readyState === WebSocket.CLOSING) {
        prev.close()
      } else if (prev.readyState === WebSocket.CONNECTING) {
        prev.onopen = () => prev.close()
        prev.onerror = null
      }
    }
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const sym   = encodeURIComponent(symbol)
    const ws    = new WebSocket(`${proto}//${window.location.host}/ws/candles/${sym}?timeframe=${tf}`)
    wsRef.current = ws

    let pollId = null

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'candle_update' && msg.candle) {
          seriesRefs.current.candle?.update(msg.candle)
          if (seriesRefs.current.volume) {
            seriesRefs.current.volume.update({
              time:  msg.candle.time,
              value: msg.candle.volume,
              color: msg.candle.close >= msg.candle.open
                ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)',
            })
          }
          setLatestOhlcv(prev => ({ ...prev, ...msg.candle, currentPrice: msg.candle.close }))
        }
      } catch { /* ignore parse errors */ }
    }

    const startPollingFallback = () => {
      if (pollId) return
      pollId = setInterval(async () => {
        try {
          // apiFetch already returns parsed JSON — don't call .json() again.
          const c = await apiFetch(`/api/v1/india/candles/${encodeURIComponent(symbol)}/latest?timeframe=${tf}`)
          if (c?.time) {
            seriesRefs.current.candle?.update(c)
            setLatestOhlcv(prev => ({ ...prev, ...c }))
          }
        } catch { /* ignore */ }
      }, 15000)
    }

    ws.onerror = startPollingFallback
    ws.onclose = (ev) => {
      if (!ev.wasClean) startPollingFallback()
    }

    // Return a cleanup function that stops both the WS and any fallback poller.
    return () => {
      // Closing a socket that is still CONNECTING triggers the browser warning
      // "WebSocket is closed before the connection is established" — common under
      // React StrictMode's mount→unmount→mount cycle in dev. Guard against it:
      // close immediately if open, otherwise defer the close until it opens.
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CLOSING) {
        ws.close()
      } else if (ws.readyState === WebSocket.CONNECTING) {
        ws.onopen = () => ws.close()
        ws.onerror = null  // suppress the benign connecting-then-closed error
      }
      if (pollId) clearInterval(pollId)
    }
  }, [symbol])

  // ── On timeframe / symbol change ─────────────────────────────────────────────
  useEffect(() => {
    loadCandles(timeframe)
    const cleanupWS = setupWS(timeframe)
    return () => {
      cleanupWS?.()
    }
  }, [symbol, timeframe, loadCandles, setupWS])

  // ── Derived display values ──────────────────────────────────────────────────
  const displayOhlcv = ohlcv || latestOhlcv
  const symClean     = symbol.replace('.NS', '').replace('^', '')
  const displayName  = name || symClean

  const prevClose = latestOhlcv?.open ?? null
  const curPrice  = latestOhlcv?.currentPrice ?? latestOhlcv?.close ?? null
  const priceDiff = (curPrice != null && prevClose != null) ? curPrice - prevClose : null
  const pricePct  = (priceDiff != null && prevClose) ? (priceDiff / prevClose) * 100 : null
  const priceUp   = priceDiff == null ? null : priceDiff >= 0

  return (
    <div
      className={[
        'flex flex-col bg-[#080e1c] rounded-xl border border-border',
        // Only clip overflow in embedded mode (used inside panels). When
        // rendered on the dedicated /chart page (fillParent) or with the
        // SignalPanel below the chart, we must NOT clip — otherwise the
        // SignalPanel bullets (Supertrend, EMA Trend, …) get hidden when
        // the viewport is shorter than the chart's natural height.
        embedded ? 'overflow-hidden' : '',
        // fillParent: grow with parent flexbox + claim full height of
        // its container. Otherwise fall back to a fixed pixel height for
        // legacy callers.
        fillParent ? 'flex-1 h-full min-h-0' : '',
      ].filter(Boolean).join(' ')}
      style={fillParent || embedded ? undefined : { height }}
    >

      {/* ── Row 1: Stock info + price ─────────────────────────────────────── */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/60 gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-slate-100 font-bold text-base">{symClean}</span>
              <span className="text-[9px] font-bold text-muted/60 border border-border/50 px-1 py-0.5 rounded">NSE</span>
            </div>
            {displayName !== symClean && (
              <p className="text-muted text-[11px] leading-tight">{displayName}</p>
            )}
          </div>
        </div>

        {/* OHLCV legend */}
        <div className="flex-1 flex justify-center">
          <OHLCVLegend data={displayOhlcv} timeframe={timeframe} />
        </div>

        <div className="flex items-center gap-4">
          {/* Current price */}
          <div className="text-right">
            <p className={`text-lg font-extrabold tabular-nums leading-tight ${priceUp == null ? 'text-slate-100' : priceUp ? 'text-profit' : 'text-loss'}`}>
              {fmtPrice(curPrice)}
            </p>
            {priceDiff != null && (
              <p className={`text-[11px] font-semibold tabular-nums ${priceUp ? 'text-profit' : 'text-loss'}`}>
                {priceUp ? '▲' : '▼'} {fmtPrice(Math.abs(priceDiff))} {pricePct != null ? `(${fmtPct(pricePct)})` : ''}
              </p>
            )}
          </div>
          {onClose && (
            <button onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-surface/60 text-muted hover:text-slate-200 transition-colors">
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {/* ── Row 2: Timeframe + indicator toggles ─────────────────────────── */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border/40 gap-4 flex-wrap">
        {/* Timeframe pills */}
        <div className="flex items-center gap-1">
          {TIMEFRAMES.map(tf => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={[
                'px-2.5 py-1 rounded text-[11px] font-bold transition-all',
                tf === timeframe
                  ? 'bg-accent text-white shadow'
                  : 'text-muted hover:text-slate-300 hover:bg-white/5',
              ].join(' ')}
            >
              {TF_LABEL[tf]}
            </button>
          ))}
          {loading && <RefreshCw size={12} className="text-muted animate-spin ml-2" />}
        </div>

        {/* Indicator toggles */}
        {showIndicators && (
          <div className="flex items-center gap-1 flex-wrap">
            {IND_CONFIG.map(({ key, label, color }) => {
              const on = indicators[key]
              return (
                <button
                  key={key}
                  onClick={() => setIndicators(prev => ({ ...prev, [key]: !prev[key] }))}
                  style={{ borderColor: on ? color : 'transparent' }}
                  className={[
                    'px-2 py-0.5 rounded text-[10px] font-semibold border transition-all',
                    on
                      ? 'text-slate-200 bg-white/5'
                      : 'text-muted bg-transparent border-border/30',
                  ].join(' ')}
                >
                  {label}
                </button>
              )
            })}

            {/* Candle count */}
            <span className="text-muted text-[10px] ml-1">
              {candleCount > 0 ? `${candleCount} candles` : ''}
            </span>
          </div>
        )}
      </div>

      {/* ── Chart area ────────────────────────────────────────────────────── */}
      <div className="flex-1 relative min-h-0">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <ChartSkeleton height="100%" symbol={symClean} timeframe={timeframe} />
          </div>
        )}
        {!loading && error && (
          <div className="absolute inset-0 z-10 p-4">
            <ChartError symbol={symClean} message={error} onRetry={() => loadCandles(timeframe)} />
          </div>
        )}
        <div
          ref={containerRef}
          className="w-full h-full"
          style={{ opacity: loading ? 0 : 1, transition: 'opacity 0.2s' }}
        />
      </div>

      {/* ── Signal panel ──────────────────────────────────────────────────── */}
      {!embedded && showSignal && (
        <div className="px-4 pb-3 pt-1 border-t border-border/40 flex-col gap-2 flex">
          <SignalPanel symbol={symbol} />
        </div>
      )}
    </div>
  )
}
