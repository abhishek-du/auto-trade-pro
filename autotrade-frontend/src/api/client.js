import axios from 'axios';

const api = axios.create({
  baseURL: 'http://localhost:8000',
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.response.use(
  (res) => res.data,
  (err) => Promise.reject(err)
);

export const getPortfolio             = ()     => api.get('/api/v1/portfolio/');
export const getPortfolioSnapshots    = ()     => api.get('/api/v1/portfolio/snapshots');
export const getPortfolioPositions    = ()     => api.get('/api/v1/portfolio/positions');
export const getTrades                = ()     => api.get('/api/v1/trades/');
export const getSignals               = ()     => api.get('/api/v1/signals/');
export const getNews                  = ()     => api.get('/api/v1/news/');
export const getAnalytics             = ()     => api.get('/api/v1/analytics/');
export const getSimulationLogs        = ()     => api.get('/api/v1/simulation/logs');
export const getSimulationPerformance = ()     => api.get('/api/v1/simulation/performance');
export const getSimulationAnalysis    = ()     => api.get('/api/v1/simulation/analysis');
export const getGoLiveStatus          = ()     => api.get('/api/v1/simulation/should-go-live');
export const triggerSignals           = ()     => api.post('/api/v1/signals/trigger');
export const getSettings              = ()     => api.get('/api/v1/settings/');
export const saveSettings             = (body) => api.post('/api/v1/settings/', body);

// ── Indian market ─────────────────────────────────────────────────────────────
export const getIndiaMarketStatus  = ()              => api.get('/api/v1/india/market-status');
export const getIndiaVix           = ()              => api.get('/api/v1/india/vix');
export const getIndiaFiiDii        = ()              => api.get('/api/v1/india/fii-dii');
export const getIndiaOptionsChain  = (symbol)        => api.get(`/api/v1/india/options-chain/${symbol}`);
export const getIndiaMutualFunds   = ()              => api.get('/api/v1/india/mutual-funds');
export const getIndiaMFSip         = (code, amt = 5000, months = 12) =>
    api.get(`/api/v1/india/mutual-funds/${code}/sip`, { params: { monthly_amount: amt, months } });
export const projectSip            = (body)          => api.post('/api/v1/india/sip/project', body);
export const listIndiaFundamentals = ()              => api.get('/api/v1/india/fundamentals');
export const getIndiaFundamentals  = (symbol)        => api.get(`/api/v1/india/fundamentals/${symbol}`);
export const getIndiaSectorPerf    = ()              => api.get('/api/v1/india/sector-performance');
export const getIndiaSignals       = (category)      => api.get('/api/v1/india/signals', {
    params: category ? { category } : {},
});
export const seedIndiaData         = ()              => api.post('/api/v1/india/seed');
export const runBacktest           = (body = {})     => api.post('/api/v1/india/backtest', body, { timeout: 120_000 });

// ── Zerodha Kite portfolio tracker (legacy) ───────────────────────────────────
export const getKiteStatus         = ()     => api.get('/api/v1/kite/status');
export const getKiteLoginUrl       = ()     => api.get('/api/v1/kite/login-url');
export const getKiteHoldings       = ()     => api.get('/api/v1/kite/holdings');
export const syncKiteHoldings      = ()     => api.post('/api/v1/kite/sync');
export const disconnectKite        = ()     => api.post('/api/v1/kite/disconnect');
export const addManualHolding      = (body) => api.post('/api/v1/kite/holdings/manual', body);

// ── Zerodha KiteConnect v3 (full integration) ─────────────────────────────────
export const getZerodhaLoginUrl    = ()     => api.get('/api/v1/zerodha/login-url');
export const getZerodhaStatus      = ()     => api.get('/api/v1/zerodha/status');
export const getZerodhaTokenStatus = ()     => api.get('/api/v1/zerodha/token-status');
export const getZerodhaMargins     = ()     => api.get('/api/v1/zerodha/margins');
export const logoutZerodha         = ()     => api.post('/api/v1/zerodha/logout');
export const getZerodhaHoldings    = ()     => api.get('/api/v1/zerodha/holdings');
export const getZerodhaPositions   = ()     => api.get('/api/v1/zerodha/positions');
export const getZerodhaOrders      = ()     => api.get('/api/v1/zerodha/orders');
export const getZerodhaTrades      = ()     => api.get('/api/v1/zerodha/trades');
export const getZerodhaPnl         = ()     => api.get('/api/v1/zerodha/pnl');
export const getZerodhaLivePrices  = (syms) => api.get('/api/v1/zerodha/live-prices', {
    params: syms ? { symbols: syms } : {},
});
export const getZerodhaMarketDepth = (sym)  => api.get(`/api/v1/zerodha/market-depth/${sym}`);
export const placeZerodhaOrder     = (body) => api.post('/api/v1/zerodha/orders', body, {
    headers: { 'X-Confirm-Real-Order': 'yes' },
});
export const cancelZerodhaOrder         = (id)      => api.delete(`/api/v1/zerodha/orders/${id}`);
export const getZerodhaWatchlistAnalysis = (symbols) =>
    api.get('/api/v1/zerodha/watchlist-analysis', {
        params: { symbols: symbols.join(',') },
        timeout: 60_000,
    });
export const getZerodhaDeepAnalysis  = (symbol) =>
    api.get(`/api/v1/zerodha/deep-analysis/${symbol}`, { timeout: 30_000 });
export const getZerodhaAutoScan      = (minScore = 25) =>
    api.get('/api/v1/zerodha/auto-scan', { params: { min_score: minScore }, timeout: 120_000 });
export const getZerodhaMfAnalysis    = () =>
    api.get('/api/v1/zerodha/mf-analysis', { timeout: 30_000 });

// ── Live NSE Market ───────────────────────────────────────────────────────────
export const getLivePrices     = ()       => api.get('/api/v1/india/live-prices');
export const getLivePrice      = (symbol) => api.get(`/api/v1/india/live-prices/${symbol}`);
export const getMarketSummary  = ()       => api.get('/api/v1/india/market-summary');
export const getIndices        = ()       => api.get('/api/v1/india/indices');
export const getTopMovers      = ()       => api.get('/api/v1/india/top-movers');
export const refreshLivePrices = ()       => api.post('/api/v1/india/live-prices/refresh');

export default api;
