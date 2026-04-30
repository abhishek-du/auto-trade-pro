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

export default api;
