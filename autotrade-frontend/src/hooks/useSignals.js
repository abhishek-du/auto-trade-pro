import { useState, useEffect, useCallback } from 'react';
import { getSignals } from '../api/client';

export function useSignals(pollInterval = 5000) {
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const fetch = useCallback(async () => {
    try {
      const data = await getSignals();
      setSignals(Array.isArray(data) ? data : data?.signals ?? []);
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

  return { signals, loading, error, refetch: fetch };
}
