import { useState, useEffect, useMemo } from 'react';
import { Newspaper, ExternalLink, Clock, TrendingUp, TrendingDown, Minus, Wifi, WifiOff, Flame, Radio, Zap, RefreshCw, AlertTriangle, ShieldAlert, Landmark, FileText, HeartHandshake, FileCheck2, Paperclip } from 'lucide-react';
import LoadingSpinner from '../components/LoadingSpinner';
import { getNews, getNewsAlerts, getCorporateAnnouncements, getSSEAnnouncements, apiFetch } from '../api/client';
import { useLivePrices } from '../contexts/LivePricesContext';

/* ── Sentiment helpers ──────────────────────────────────────── */
function sentimentMeta(raw) {
  const s = (raw ?? '').toString().toUpperCase();
  if (s === 'POSITIVE' || s === 'BULLISH' || Number(raw) > 0.2)
    return { label: 'Bullish', cls: 'bg-profit/15 text-profit border-profit/30', Icon: TrendingUp };
  if (s === 'NEGATIVE' || s === 'BEARISH' || Number(raw) < -0.2)
    return { label: 'Bearish', cls: 'bg-loss/15 text-loss border-loss/30', Icon: TrendingDown };
  return { label: 'Neutral', cls: 'bg-neutral/15 text-neutral border-neutral/30', Icon: Minus };
}

function SentimentBadge({ sentiment }) {
  const { label, cls, Icon } = sentimentMeta(sentiment);
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${cls}`}>
      <Icon size={11} />{label}
    </span>
  );
}

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

  const pct   = ((score + 1) / 2) * 100;
  const label = score > 0.15 ? 'Overall Bullish' : score < -0.15 ? 'Overall Bearish' : 'Neutral Sentiment';
  const color = score > 0.15 ? '#10B981' : score < -0.15 ? '#EF4444' : '#6B7280';

  return (
    <div className="glass-panel border border-border rounded-xl p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-slate-200 font-semibold text-sm">Market Sentiment Gauge</h3>
        <span className="text-xs font-bold" style={{ color }}>{label}</span>
      </div>
      <div className="relative h-4 bg-gradient-to-r from-loss via-neutral to-profit rounded-full overflow-hidden">
        <div className="absolute top-0 bottom-0 w-1 bg-white rounded-full shadow-lg transition-all"
          style={{ left: `calc(${pct}% - 2px)` }} />
      </div>
      <div className="flex justify-between text-xs text-muted">
        <span>Bearish</span><span>Neutral</span><span>Bullish</span>
      </div>
    </div>
  );
}

/* ── Market Alerts strip (high-impact shock/geopolitical news) ─── */
function MarketAlertsStrip() {
  const [alerts, setAlerts]   = useState([]);
  const [loading, setLoading] = useState(true);

  // setState only inside async callbacks (not synchronously in the effect body)
  const fetchAlerts = () =>
    getNewsAlerts()
      .then((d) => setAlerts(Array.isArray(d) ? d : []))
      .catch(() => setAlerts([]))
      .finally(() => setLoading(false));

  useEffect(() => {
    fetchAlerts();
    const id = setInterval(fetchAlerts, 120000); // re-check every 2 min
    return () => clearInterval(id);
  }, []);

  const refresh = () => { setLoading(true); fetchAlerts(); };

  // Calm state — slim, non-intrusive bar so the feature stays discoverable.
  if (!loading && alerts.length === 0) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted glass-panel border border-border rounded-xl px-4 py-2.5">
        <ShieldAlert size={13} className="text-profit" />
        <span className="text-slate-300 font-medium">No high-impact market alerts right now.</span>
        <span className="opacity-60">Monitoring for shock / geopolitical headlines.</span>
      </div>
    );
  }
  if (loading && alerts.length === 0) return null;

  return (
    <div className="glass-panel border border-loss/40 rounded-xl p-4 space-y-3 relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-br from-loss/10 via-transparent to-transparent pointer-events-none" />
      <div className="flex items-center justify-between relative">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-loss/15 border border-loss/40 flex items-center justify-center">
            <AlertTriangle size={16} className="text-loss animate-pulse" />
          </div>
          <div>
            <h3 className="text-loss font-bold text-sm">🔴 Market Alerts</h3>
            <p className="text-[10px] text-muted uppercase tracking-widest">
              High-impact shock headlines · {alerts.length} active
            </p>
          </div>
        </div>
        <button onClick={refresh} className="p-1.5 rounded-lg hover:bg-surface text-muted hover:text-loss transition-colors" title="Refresh alerts">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      <div className="space-y-1.5 relative">
        {alerts.map((a, i) => (
          <a key={a.id ?? a.url ?? i}
            href={a.url || undefined} target="_blank" rel="noopener noreferrer"
            className="flex items-start gap-2 text-xs bg-loss/5 hover:bg-loss/10 border border-loss/20 rounded-lg px-3 py-2 transition-colors">
            <TrendingDown size={13} className="text-loss shrink-0 mt-0.5" />
            <span className="text-slate-200 flex-1 leading-snug">{a.headline}</span>
            <span className="text-muted shrink-0 whitespace-nowrap hidden sm:inline">{a.source}</span>
          </a>
        ))}
      </div>
    </div>
  );
}

/* ── NSE Corporate Announcements (separate from the RSS feed) ─── */
function CorporateAnnouncements() {
  const [items, setItems]     = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchItems = () =>
    getCorporateAnnouncements()
      .then((d) => setItems(Array.isArray(d) ? d : []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));

  useEffect(() => {
    fetchItems();
    const id = setInterval(fetchItems, 60000); // matches the poller's own 60s cadence
    return () => clearInterval(id);
  }, []);

  const refresh = () => { setLoading(true); fetchItems(); };

  return (
    <div className="glass-panel border border-border rounded-xl p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-accent/15 border border-accent/40 flex items-center justify-center">
            <Landmark size={16} className="text-accent" />
          </div>
          <div>
            <h3 className="text-slate-100 font-bold text-sm">NSE Corporate Announcements</h3>
            <p className="text-[10px] text-muted uppercase tracking-widest">
              Financial results · M&amp;A · dividends · credit rating · buybacks — routine filings excluded
            </p>
          </div>
        </div>
        <button onClick={refresh} className="p-1.5 rounded-lg hover:bg-surface text-muted hover:text-accent transition-colors" title="Refresh announcements">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {loading && items.length === 0 ? (
        <div className="text-xs text-muted animate-pulse py-2">Polling NSE for high-impact filings…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-muted py-2">
          No high-impact NSE announcements captured yet.
          <span className="ml-1 text-accent">Checked every 60 seconds.</span>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((a) => {
            const symbol = a.tickers_affected?.[0];
            return (
              <div key={a.id} className="flex items-start gap-3 bg-surface/40 hover:bg-surface/70 border border-border/60 rounded-lg px-3 py-2.5 transition-colors">
                <FileText size={14} className="text-accent shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-slate-200 font-semibold text-sm truncate">{a.company || symbol}</span>
                    {symbol && (
                      <span className="bg-accent/20 text-accent text-xs px-1.5 py-0.5 rounded font-mono">{symbol}</span>
                    )}
                    {a.category && (
                      <span className="text-[10px] font-bold text-amber-400 border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 rounded-full">
                        {a.category}
                      </span>
                    )}
                    <SentimentBadge sentiment={a.sentiment} />
                  </div>
                  <p className="text-muted text-xs leading-relaxed line-clamp-2">{a.headline}</p>
                  <div className="flex items-center gap-1.5 text-muted text-xs">
                    <Clock size={11} />
                    <span>{relTime(a.published_at ?? a.crawled_at)}</span>
                  </div>
                </div>
                {a.url && (
                  <a href={a.url} target="_blank" rel="noopener noreferrer"
                    className="p-1.5 text-muted hover:text-accent rounded-lg hover:bg-surface transition-colors shrink-0"
                    title="Open NSE filing PDF">
                    <ExternalLink size={13} />
                  </a>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── NSE Social Stock Exchange announcements (NPOs/Social Enterprises) ─── */
/* Informational only — not routed to execution, see backend comment on
   SSEAnnouncement / sync_sse_announcements for why. Shows every field NSE
   returns (comp_name, symbol, an_desc, text, PDF, file size, XBRL flag,
   filing vs recorded timestamp and the latency between them) rather than
   condensing them into a single headline the way the RSS feed is. */
function SSEAnnouncements() {
  const [items, setItems]     = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchItems = () =>
    getSSEAnnouncements()
      .then((d) => setItems(Array.isArray(d) ? d : []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));

  useEffect(() => {
    fetchItems();
    const id = setInterval(fetchItems, 300000); // matches the 10-min poller
    return () => clearInterval(id);
  }, []);

  const refresh = () => { setLoading(true); fetchItems(); };

  return (
    <div className="glass-panel border border-border rounded-xl p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-pink-500/15 border border-pink-500/40 flex items-center justify-center">
            <HeartHandshake size={16} className="text-pink-400" />
          </div>
          <div>
            <h3 className="text-slate-100 font-bold text-sm">Social Stock Exchange Announcements</h3>
            <p className="text-[10px] text-muted uppercase tracking-widest">
              NPO &amp; Social Enterprise filings · informational only, not traded
            </p>
          </div>
        </div>
        <button onClick={refresh} className="p-1.5 rounded-lg hover:bg-surface text-muted hover:text-pink-400 transition-colors" title="Refresh SSE filings">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {loading && items.length === 0 ? (
        <div className="text-xs text-muted animate-pulse py-2">Polling NSE Social Stock Exchange filings…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-muted py-2">
          No SSE filings captured yet.
          <span className="ml-1 text-pink-400">Checked every 10 minutes.</span>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((a) => (
            <div key={a.id} className="bg-surface/40 hover:bg-surface/70 border border-border/60 rounded-lg px-3 py-2.5 transition-colors space-y-1.5">
              <div className="flex items-start gap-3">
                <FileText size={14} className="text-pink-400 shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-slate-200 font-semibold text-sm truncate">{a.comp_name || 'Unknown entity'}</span>
                    {a.symbol && (
                      <span className="bg-pink-500/20 text-pink-300 text-xs px-1.5 py-0.5 rounded font-mono">{a.symbol}</span>
                    )}
                    {a.an_desc && (
                      <span className="text-[10px] font-bold text-amber-400 border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 rounded-full">
                        {a.an_desc}
                      </span>
                    )}
                    {a.has_xbrl && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-blue-400 border border-blue-400/30 bg-blue-400/10 px-1.5 py-0.5 rounded-full">
                        <FileCheck2 size={9} />XBRL
                      </span>
                    )}
                    <SentimentBadge sentiment={a.sentiment} />
                  </div>
                  {a.text && <p className="text-muted text-xs leading-relaxed">{a.text}</p>}
                  <div className="flex items-center gap-3 text-muted text-[11px] flex-wrap">
                    <span className="inline-flex items-center gap-1"><Clock size={11} />{relTime(a.ann_date ?? a.crawled_at)}</span>
                    {a.att_file_size && (
                      <span className="inline-flex items-center gap-1"><Paperclip size={11} />{a.att_file_size}</span>
                    )}
                    {a.diff_time && (
                      <span title="Time between filing and NSE recording it">recorded +{a.diff_time}</span>
                    )}
                  </div>
                </div>
                {a.an_attach && (
                  <a href={a.an_attach} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-pink-300 border border-pink-500/30 bg-pink-500/10 hover:bg-pink-500/20 rounded-lg transition-colors shrink-0"
                    title="Open filing PDF">
                    <ExternalLink size={12} />PDF
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Narrative Intelligence Panel (Eagle Eyes style) ─────────── */
function NarrativePanel() {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = () => {
    setLoading(true);
    apiFetch('/api/v1/news/narrative')
      .then(d => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => { refresh(); }, []);

  const boostColor = (boost) => {
    if (boost >= 20) return 'text-profit border-profit/40 bg-profit/10';
    if (boost >= 12) return 'text-yellow-400 border-yellow-400/40 bg-yellow-400/10';
    return 'text-blue-400 border-blue-400/40 bg-blue-400/10';
  };

  return (
    <div className="glass-panel border border-accent/30 rounded-xl p-5 space-y-4 relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-br from-violet-500/5 via-transparent to-orange-500/5 pointer-events-none" />
      <div className="flex items-center justify-between relative">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-orange-500/20 to-violet-500/20 border border-orange-500/30 flex items-center justify-center">
            <Flame size={16} className="text-orange-400" />
          </div>
          <div>
            <h3 className="text-slate-100 font-bold text-sm">🦅 Narrative Intelligence</h3>
            <p className="text-[10px] text-muted uppercase tracking-widest">Eagle Eyes · Live Sector Themes from RSS + Telegram</p>
          </div>
        </div>
        <button onClick={refresh} className="p-1.5 rounded-lg hover:bg-surface text-muted hover:text-accent transition-colors" title="Refresh narrative cache">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {loading ? (
        <div className="text-xs text-muted animate-pulse py-2">Scanning RSS + Telegram feeds…</div>
      ) : !data || data.total_hot_sectors === 0 ? (
        <div className="text-xs text-muted py-2">
          No strong sector narratives detected right now.
          <span className="ml-1 text-accent">Cache refreshes every 5 minutes during market hours.</span>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.hot_sectors).map(([sector, info]) => (
              <div key={sector}
                className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-semibold ${boostColor(info.boost)}`}>
                <Zap size={11} />
                <span>{sector}</span>
                <span className="opacity-70">+{info.boost}pts</span>
              </div>
            ))}
          </div>
          <div className="space-y-1.5 bg-surface/40 rounded-lg p-3">
            {Object.entries(data.hot_sectors).map(([sector, info]) => (
              <div key={sector} className="flex items-start gap-2 text-xs">
                <span className="text-orange-400 font-semibold shrink-0 w-24">{sector}</span>
                <span className="text-muted">{info.reason}</span>
              </div>
            ))}
          </div>
          {data.last_updated && (
            <div className="flex items-center gap-1.5 text-[10px] text-muted border-t border-border/50 pt-2">
              <Radio size={9} className="text-profit animate-pulse" />
              <span>
                Last refreshed: {new Date(data.last_updated).toLocaleTimeString('en-IN')}
                {data.cache_age_seconds != null ? ` · ${Math.floor(data.cache_age_seconds / 60)}m ${data.cache_age_seconds % 60}s ago` : ''}
              </span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ── Source breakdown ─────────────────────────────────────────── */
function SourceBreakdown({ articles }) {
  const counts = useMemo(() => {
    const map = {};
    for (const a of articles) {
      const src = a.source || 'Unknown';
      map[src] = (map[src] || 0) + 1;
    }
    return Object.entries(map).sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [articles]);

  if (!counts.length) return null;
  const total = articles.length;

  return (
    <div className="glass-panel border border-border rounded-xl p-4">
      <h3 className="text-slate-300 font-semibold text-xs uppercase tracking-widest mb-3 flex items-center gap-1.5">
        <Radio size={11} className="text-accent" />
        News Sources
      </h3>
      <div className="space-y-1.5">
        {counts.map(([src, cnt]) => {
          const pct = Math.round((cnt / total) * 100);
          return (
            <div key={src} className="flex items-center gap-2 text-xs">
              <span className="text-muted w-36 truncate" title={src}>{src}</span>
              <div className="flex-1 h-1.5 bg-surface rounded-full overflow-hidden">
                <div className="h-full bg-accent/60 rounded-full transition-all" style={{ width: `${pct}%` }} />
              </div>
              <span className="text-muted w-6 text-right font-mono">{cnt}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function relTime(dateStr) {
  if (!dateStr) return '';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1)  return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs  < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const SENTIMENT_FILTERS = ['All', 'Bullish', 'Bearish', 'Neutral'];
const MAX_ARTICLES = 200;

export default function News() {
  const [articles,  setArticles]  = useState([]);
  const [loading,   setLoading]   = useState(true);
  const [filter,    setFilter]    = useState('All');
  const [search,    setSearch]    = useState('');
  const [liveCount, setLiveCount] = useState(0);

  useEffect(() => {
    getNews()
      .then((d) => setArticles(Array.isArray(d) ? d : d?.articles ?? []))
      .catch(() => setArticles([]))
      .finally(() => setLoading(false));
  }, []);

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

  const normalised = useMemo(() =>
    articles.map((a) => ({
      ...a,
      title:           a.title   ?? a.headline ?? 'Untitled',
      symbols:         a.symbols ?? a.tickers_affected ?? [],
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

      {/* High-impact market alerts (shock / geopolitical news) — surfaced on top */}
      <MarketAlertsStrip />

      {/* NSE corporate announcements — separate source from the RSS feed below */}
      <CorporateAnnouncements />

      {/* NSE Social Stock Exchange (NPO) announcements — its own section, its own table */}
      <SSEAnnouncements />

      {/* Two-col: sentiment gauge + source breakdown */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="md:col-span-2">
          <SentimentGauge articles={normalised} />
        </div>
        <SourceBreakdown articles={normalised} />
      </div>

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
            <button key={f} onClick={() => setFilter(f)}
              className={['px-3 py-2 text-xs font-medium transition-colors',
                filter === f ? 'bg-accent text-white' : 'text-muted hover:text-slate-300 hover:bg-surface',
              ].join(' ')}>
              {f}
            </button>
          ))}
        </div>
        <span className="text-muted text-xs">{filtered.length} articles</span>
        <span
          className={['inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-full border',
            wsStatus === 'connected'
              ? 'text-profit border-profit/30 bg-profit/10'
              : 'text-muted border-border bg-surface',
          ].join(' ')}
          title={wsStatus === 'connected'
            ? `Live feed connected${liveCount ? ` · ${liveCount} new since open` : ''}`
            : 'Live feed disconnected — using last fetch'}
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
            <article key={a.id ?? a.url ?? i}
              className={['glass-panel border rounded-xl p-5 hover:border-accent/40 transition-colors group',
                a.high_impact ? 'border-loss/40 shadow-[0_0_0_1px_rgba(239,68,68,0.15)]'
                  : a._live ? 'border-accent/50 shadow-[0_0_0_1px_rgba(99,102,241,0.15)]' : 'border-border',
              ].join(' ')}>
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0 space-y-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <SentimentBadge sentiment={a.sentiment} />
                    {a.high_impact && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-loss border border-loss/40 bg-loss/10 px-1.5 py-0.5 rounded-full">
                        <AlertTriangle size={9} />HIGH IMPACT
                      </span>
                    )}
                    {a._live && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-accent border border-accent/30 bg-accent/10 px-1.5 py-0.5 rounded-full animate-pulse">
                        <Radio size={8} />LIVE
                      </span>
                    )}
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
                    <span>{relTime(a.published_at ?? a.crawled_at ?? a.date)}</span>
                  </div>
                </div>
                {a.url && (
                  <a href={a.url} target="_blank" rel="noopener noreferrer"
                    className="p-2 text-muted hover:text-accent rounded-lg hover:bg-surface transition-colors shrink-0 opacity-0 group-hover:opacity-100"
                    title="Open article">
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
