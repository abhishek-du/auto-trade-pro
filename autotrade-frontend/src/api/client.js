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

export default api;
