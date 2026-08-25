import asyncio, sys
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from q import main
asyncio.run(main([
("1m candle coverage per session (last 45 days)",
 """SELECT timestamp::date d, COUNT(DISTINCT symbol) syms, COUNT(*) bars,
      MIN(timestamp)::time first_bar, MAX(timestamp)::time last_bar
    FROM candles WHERE timeframe='1m' AND timestamp > CURRENT_DATE - 45
    GROUP BY 1 ORDER BY 1"""),
("tactical_signals per session (ALL history)",
 """SELECT created_at::date d, COUNT(*) n, COUNT(DISTINCT symbol) syms,
      COUNT(DISTINCT strategy) rules, COUNT(*) FILTER (WHERE executed) execd
    FROM tactical_signals GROUP BY 1 ORDER BY 1"""),
("agent_decisions per session (ALL history)",
 "SELECT ts::date d, COUNT(*) n, COUNT(DISTINCT symbol) syms, COUNT(*) FILTER (WHERE action='TAKE') takes, COUNT(master_score) ms FROM agent_decisions GROUP BY 1 ORDER BY 1"),
("causal_events per session",
 "SELECT created_at::date d, COUNT(*) n FROM causal_events WHERE created_at > CURRENT_DATE - 45 GROUP BY 1 ORDER BY 1"),
("news_items per session",
 "SELECT crawled_at::date d, COUNT(*) n, COUNT(DISTINCT source) srcs FROM news_items WHERE crawled_at > CURRENT_DATE - 45 GROUP BY 1 ORDER BY 1"),
("paper_trades per session",
 "SELECT opened_at::date d, COUNT(*) n, COUNT(*) FILTER (WHERE closed_at IS NOT NULL) closed FROM paper_trades WHERE opened_at > CURRENT_DATE - 45 GROUP BY 1 ORDER BY 1"),
("master_intelligence_scores — does the table exist and what is in it?",
 "SELECT COUNT(*) rows, MIN(scored_at)::date, MAX(scored_at)::date, COUNT(DISTINCT symbol) syms FROM master_intelligence_scores"),
]))
