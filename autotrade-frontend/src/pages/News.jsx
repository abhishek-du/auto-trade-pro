import { useState, useEffect, useMemo } from 'react';
import { Newspaper, ExternalLink, Clock, TrendingUp, TrendingDown, Minus, Wifi, WifiOff } from 'lucide-react';
import LoadingSpinner from '../components/LoadingSpinner';
import { getNews } from '../api/client';
import { useLivePrices } from '../contexts/LivePricesContext';

/* ── Sentiment helpers ──────────────────────────────────────── */

function sentimentMeta(raw) {
  const s = (raw ?? '').toString().toUpperCase();
  if (s === 'POSITIVE' || s === 'BULLISH' || Number(raw) > 0.2) {
    return { label: 'Bullish', cls: 'bg-profit/15 text-profit border-profit/30', Icon: TrendingUp };
  }
  if (s === 'NEGATIVE' || s === 'BEARISH' || Number(raw) < -0.2) {
    return { label: 'Bearish', cls: 'bg-loss/15 text-loss border-loss/30', Icon: TrendingDown };
  }
  return { label: 'Neutral', cls: 'bg-neutral/15 text-neutral border-neutral/30', Icon: Minus };
}

function SentimentBadge({ sentiment }) {
  const { label, cls, Icon } = sentimentMeta(sentiment);
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${cls}`}>
      <Icon size={11} />
      {label}
    </span>
  );
}

/* overall sentiment gauge (–100 to +100 mapped from avg score) */
function SentimentGauge({ articles }) {
  const score = useMemo(() => {
    if (!articles.length) return 0;
    const sum = articles.reduce((acc, a) => {
      const s = (a.sentiment ?? '').toString().toUpperCase();
      if (s === 'POSITIVE' || s === 'BULLISH') return acc + 1;
      if (s === 'NEGATIVE' || s === 'BEARISH') return acc - 1;
      const n = Number(a.sentiment_score ?? a.sentiment ?? 0);
      return acc + (isNaN(n) ? 0 : n);
    }, 0);
    return Math.max(-1, Math.min(1, sum / articles.length));
  }, [articles]);

  const pct     = ((score + 1) / 2) * 100;
  const label   = score > 0.15 ? 'Overall Bullish' : score < -0.15 ? 'Overall Bearish' : 'Neutral Sentiment';
  const color   = score > 0.15 ? '#10B981' : score < -0.15 ? '#EF4444' : '#6B7280';

  return (
    <div className="glass-panel border border-border rounded-xl p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-slate-200 font-semibold text-sm">Market Sentiment Gauge</h3>
        <span className="text-xs font-bold" style={{ color }}>{label}</span>
      </div>
      <div className="relative h-4 bg-gradient-to-r from-loss via-neutral to-profit rounded-full overflow-hidden">
        <div
          className="absolute top-0 bottom-0 w-1 bg-white rounded-full shadow-lg transition-all"
          style={{ left: `calc(${pct}% - 2px)` }}
        />
      </div>
      <div className="flex justify-between text-xs text-muted">
        <span>Bearish</span>
        <span>Neutral</span>
        <span>Bullish</span>
      </div>
    </div>
  );
}

/* format relative time */
function relTime(dateStr) {
  if (!dateStr) return '';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1)   return 'Just now';
  if (mins < 60)  return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs  < 24)  return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/* ── Page ───────────────────────────────────────────────────── */

const SENTIMENT_FILTERS = ['All', 'Bullish', 'Bearish', 'Neutral'];

const MAX_ARTICLES = 200;

export default function News() {
  const [articles,  setArticles]  = useState([]);
  const [loading,   setLoading]   = useState(true);
  const [filter,    setFilter]    = useState('All');
  const [search,    setSearch]    = useState('');
  const [liveCount, setLiveCount] = useState(0);

  // ── Initial REST fetch — load the existing feed on mount ───────────────────
  useEffect(() => {
    getNews()
      .then((d) => setArticles(Array.isArray(d) ? d : d?.articles ?? []))
      .catch(() => setArticles([]))
      .finally(() => setLoading(false));
  }, []);

  // ── Live feed via shared WebSocket context ────────────────────────────────
  const { connected, lastNewsItem } = useLivePrices();
  const wsStatus = connected ? 'open' : 'closed';

  useEffect(() => {
    if (!lastNewsItem) return;
    setArticles((prev) => {
      const url = lastNewsItem.url;
      if (url && prev.some((a) => a.url === url)) return prev;
      const next = [{ ...lastNewsItem, _live: true }, ...prev];
      return next.length > MAX_ARTICLES ? next.slice(0, MAX_ARTICLES) : next;
    });
    setLiveCount((n) => n + 1);
  }, [lastNewsItem]);

  // Normalise backend shape: headline→title, tickers_affected→symbols, score→sentiment_score
  const normalised = useMemo(() =>
    articles.map((a) => ({
      ...a,
      title:          a.title   ?? a.headline ?? 'Untitled',
      symbols:        a.symbols ?? a.tickers_affected ?? [],
      sentiment_score: a.sentiment_score ?? a.score ?? 0,
    })),
  [articles]);

  const filtered = useMemo(() => {
    return normalised.filter((a) => {
      const { label } = sentimentMeta(a.sentiment);
      if (filter !== 'All' && label !== filter) return false;
      if (search) {
        const hay = `${a.title ?? ''} ${a.source ?? ''}`.toLowerCase();
        if (!hay.includes(search.toLowerCase())) return false;
      }
      return true;
    });
  }, [normalised, filter, search]);

  if (loading) return <LoadingSpinner message="Fetching market news…" />;

  return (
    <div className="space-y-6">

      {/* Gauge — pass normalised articles so score field is consistent */}
      <SentimentGauge articles={normalised} />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-40">
          <Newspaper size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            placeholder="Search news…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full glass-panel border border-border rounded-lg pl-8 pr-3 py-2 text-sm text-slate-200 placeholder:text-muted focus:outline-none focus:border-accent"
          />
        </div>
        <div className="flex rounded-lg overflow-hidden border border-border">
          {SENTIMENT_FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={[
                'px-3 py-2 text-xs font-medium transition-colors',
                filter === f ? 'bg-accent text-white' : 'text-muted hover:text-slate-300 hover:bg-surface',
              ].join(' ')}
            >
              {f}
            </button>
          ))}
        </div>
        <span className="text-muted text-xs">{filtered.length} articles</span>
        {/* Live-feed indicator — also acts as a passive WS health check */}
        <span
          className={[
            'inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-full border',
            wsStatus === 'connected'
              ? 'text-profit border-profit/30 bg-profit/10'
              : 'text-muted border-border bg-surface',
          ].join(' ')}
          title={
            wsStatus === 'connected'
              ? `Live feed connected${liveCount ? ` · ${liveCount} new since open` : ''}`
              : 'Live feed disconnected — using last fetch'
          }
        >
          {wsStatus === 'connected' ? <Wifi size={11} /> : <WifiOff size={11} />}
          {wsStatus === 'connected' ? 'Live' : 'Offline'}
          {liveCount > 0 && wsStatus === 'connected' ? ` · ${liveCount}` : ''}
        </span>
      </div>

      {/* Article list */}
      {filtered.length === 0 ? (
        <div className="glass-panel border border-border rounded-xl p-10 text-center text-muted text-sm">
          No news articles match the current filter.
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((a, i) => (
            <article
              key={a.id ?? a.url ?? i}
              className={[
                'glass-panel border rounded-xl p-5 hover:border-accent/40 transition-colors group',
                a._live ? 'border-accent/50 shadow-[0_0_0_1px_rgba(99,102,241,0.15)]' : 'border-border',
              ].join(' ')}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0 space-y-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <SentimentBadge sentiment={a.sentiment} />
                    {a.source && (
                      <span className="text-muted text-xs font-medium">{a.source}</span>
                    )}
                    {a.symbols?.length > 0 && (
                      <div className="flex gap-1">
                        {a.symbols.slice(0, 3).map((sym) => (
                          <span key={sym} className="bg-accent/20 text-accent text-xs px-1.5 py-0.5 rounded font-mono">
                            {sym}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <h4 className="text-slate-200 font-medium text-sm leading-snug">
                    {a.title ?? 'Untitled article'}
                  </h4>
                  {a.summary && (
                    <p className="text-muted text-xs leading-relaxed line-clamp-2">{a.summary}</p>
                  )}
                  <div className="flex items-center gap-1.5 text-muted text-xs">
                    <Clock size={11} />
                    <span>{relTime(a.published_at ?? a.date)}</span>
                  </div>
                </div>
                {a.url && (
                  <a
                    href={a.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-2 text-muted hover:text-accent rounded-lg hover:bg-surface transition-colors shrink-0 opacity-0 group-hover:opacity-100"
                    title="Open article"
                  >
                    <ExternalLink size={14} />
                  </a>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
