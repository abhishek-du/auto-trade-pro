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
    <div className="rounded-xl border border-border p-8 text-center glass-panel">
      <p className="text-muted text-sm">No news articles loaded yet</p>
    </div>
  );

  return (
    <div className="space-y-2">
      {articles.map((a, i) => {
        let title = a.headline ?? a.title;
        let llmSummary = "";
        if (title && title.includes("| [LLM Summary: ")) {
          const parts = title.split("| [LLM Summary: ");
          title = parts[0].trim();
          llmSummary = parts[1].replace(/]$/, "").trim();
        }

        return (
          <div key={i}
            className="rounded-xl border border-border px-4 py-3.5 flex gap-4 items-start hover:border-accent/25 transition-all duration-150 group cursor-default glass-panel">
            <div className="flex-1 min-w-0">
              <p className="text-slate-200 text-sm font-medium leading-snug group-hover:text-white transition-colors">
                {title}
              </p>
              
              {llmSummary && (
                <div className="mt-2.5 mb-2 pl-3 border-l-2 border-accent/40 bg-accent/5 p-2 rounded-r-md">
                  <p className="text-xs text-accent/90 flex items-center gap-1 font-semibold mb-0.5">
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    AI Impact Analysis
                  </p>
                  <p className="text-xs text-slate-300 leading-relaxed italic">{llmSummary}</p>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-3 mt-2">
                <span className="text-muted text-[11px] bg-white/5 px-2 py-0.5 rounded border border-white/5">{a.source}</span>
                {a.company && <span className="text-blue-400/80 text-[11px] font-semibold">{a.company}</span>}
                {a.category && <span className="text-purple-400/80 text-[11px]">{a.category}</span>}
                {a.published_at && <span className="text-muted/60 text-[11px]">{fmtDate(a.published_at)}</span>}
              </div>
            </div>
            <div className="flex flex-col items-end gap-2 shrink-0 min-w-[70px]">
              {a.sentiment && (
                <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border tracking-wider w-full text-center ${sentimentCls(a.sentiment)}`}>
                  {a.sentiment}
                </span>
              )}
              {a.score !== undefined && a.score !== 0 && (
                <span className="text-[10px] text-muted/80 w-full text-center">
                  Score: <span className="text-white/80 font-medium">{(a.score * 100).toFixed(0)}</span>
                </span>
              )}
              {a.url && (
                <a href={a.url} target="_blank" rel="noopener noreferrer"
                  className="mt-1 px-3 py-1 bg-accent/10 hover:bg-accent/20 border border-accent/20 rounded-md text-accent text-[11px] font-medium transition-colors w-full text-center flex items-center justify-center gap-1">
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg>
                  PDF
                </a>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
