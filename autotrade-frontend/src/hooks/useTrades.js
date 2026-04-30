import { useState, useEffect, useCallback } from 'react';
import { getTrades } from '../api/client';

export function useTrades(pollInterval = 15000) {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const fetch = useCallback(async () => {
    try {
      const data = await getTrades();
      setTrades(Array.isArray(data) ? data : data?.trades ?? []);
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch();
    const id = setInterval(fetch, pollInterval);
    return () => clearInterval(id);
  }, [fetch, pollInterval]);

  return { trades, loading, error, refetch: fetch };
}
