import { useState, useEffect, useCallback } from 'react';
import { getPortfolio } from '../api/client';

export function usePortfolio(pollInterval = 10000) {
  const [portfolio, setPortfolio] = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);

  const fetch = useCallback(async () => {
    try {
      const data = await getPortfolio();
      setPortfolio(data);
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

  return { portfolio, loading, error, refetch: fetch };
}
