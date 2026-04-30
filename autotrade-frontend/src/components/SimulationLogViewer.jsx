import { useState, useMemo, useRef, useEffect } from 'react';
import { Terminal } from 'lucide-react';

const FILTERS = ['All', 'Trades', 'Analysis'];

const TRADE_KEYWORDS   = ['BUY', 'SELL', 'TRADE', 'EXECUTED', 'FILLED', 'POSITION'];
const ANALYSIS_KEYWORDS = ['SIGNAL', 'ANALYSIS', 'SCORE', 'INDICATOR', 'PATTERN', 'SENTIMENT', 'REJECTED'];

function lineFilter(line, filter) {
  const up = line.toUpperCase();
  if (filter === 'Trades')   return TRADE_KEYWORDS.some((k) => up.includes(k));
  if (filter === 'Analysis') return ANALYSIS_KEYWORDS.some((k) => up.includes(k));
  return true;
}

function lineColor(line) {
  const up = line.toUpperCase();
  if (up.includes('ERROR') || up.includes('FAIL'))              return 'text-loss';
  if (up.includes('BUY')   || up.includes('PROFIT') || up.includes('WIN'))  return 'text-profit';
  if (up.includes('SELL')  || up.includes('LOSS'))              return 'text-loss/90';
  if (up.includes('WARN')  || up.includes('REJECT'))            return 'text-warn';
  if (up.includes('SIGNAL') || up.includes('ANALYSIS'))         return 'text-accent';
  return 'text-slate-400';
}

function linePrefix(line) {
  const up = line.toUpperCase();
  if (up.includes('ERROR'))                                 return '✗ ';
  if (up.includes('BUY') || up.includes('SELL') || up.includes('TRADE')) return '● ';
  if (up.includes('SIGNAL') || up.includes('ANALYSIS'))    return '◆ ';
  return '  ';
}

export default function SimulationLogViewer({ logs = [] }) {
  const [filter, setFilter] = useState('All');
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef(null);

  const filtered = useMemo(
    () => logs.filter((l) => lineFilter(l, filter)),
    [logs, filter]
  );

  useEffect(() => {
    if (autoScroll) bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [filtered, autoScroll]);

  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2 text-muted">
          <Terminal size={14} />
          <span className="text-xs font-medium uppercase tracking-wider">AI Decision Log</span>
          <span className="text-xs bg-surface px-2 py-0.5 rounded-full tabular-nums">
            {filtered.length}/{logs.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-muted text-xs cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="w-3 h-3 accent-accent"
            />
            Auto-scroll
          </label>
          <div className="flex rounded-lg overflow-hidden border border-border">
            {FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={[
                  'px-3 py-1.5 text-xs font-medium transition-colors',
                  filter === f
                    ? 'bg-accent text-white'
                    : 'text-muted hover:text-slate-300 hover:bg-surface',
                ].join(' ')}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Log output */}
      <div className="h-72 overflow-y-auto font-mono text-xs p-3 space-y-px bg-surface/50">
        {filtered.length === 0 ? (
          <span className="text-muted italic">No log entries match the selected filter.</span>
        ) : (
          filtered.map((line, i) => (
            <div key={i} className={`leading-relaxed whitespace-pre-wrap ${lineColor(line)}`}>
              <span className="select-none opacity-50 mr-1">{String(i + 1).padStart(4, '0')}</span>
              <span className="select-none mr-1">{linePrefix(line)}</span>
              {line}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
