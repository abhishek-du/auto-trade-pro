const fmtDate = (s) => {
  try { return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  catch { return ''; }
};

function sentimentCls(s) {
  if (!s || s === 'neutral') return 'text-muted border-muted/15 bg-white/3';
  if (s === 'positive') return 'text-profit border-profit/20 bg-profit/6';
  return 'text-loss border-loss/20 bg-loss/6';
}

export default function NewsPanel({ articles = [] }) {
  if (!articles.length) return (
    <div className="rounded-xl border border-border p-8 text-center" style={{ background: '#0F1829' }}>
      <p className="text-muted text-sm">No news articles loaded yet</p>
    </div>
  );

  return (
    <div className="space-y-2">
      {articles.map((a, i) => (
        <div key={i}
          className="rounded-xl border border-border px-4 py-3.5 flex gap-4 items-start hover:border-accent/25 transition-all duration-150 group cursor-default"
          style={{ background: 'linear-gradient(135deg,#0F1829,#131E30)' }}>
          <div className="flex-1 min-w-0">
            <p className="text-slate-200 text-sm font-medium leading-snug group-hover:text-white transition-colors line-clamp-2">
              {a.headline ?? a.title}
            </p>
            <div className="flex items-center gap-3 mt-1.5">
              <span className="text-muted text-xs">{a.source}</span>
              {a.published_at && <span className="text-muted/50 text-[10px]">{fmtDate(a.published_at)}</span>}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            {a.sentiment && (
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase border ${sentimentCls(a.sentiment)}`}>
                {a.sentiment}
              </span>
            )}
            {a.url && (
              <a href={a.url} target="_blank" rel="noopener noreferrer"
                className="text-accent/50 hover:text-accent text-[10px] transition-colors">
                Read →
              </a>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
