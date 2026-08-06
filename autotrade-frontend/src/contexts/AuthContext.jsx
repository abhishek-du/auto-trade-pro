import { createContext, useContext, useState, useCallback, useEffect } from 'react';

const AuthContext = createContext(null);

const TOKEN_KEY = 'atp_admin_token';

export function AuthProvider({ children }) {
  const [token, setToken]   = useState(() => localStorage.getItem(TOKEN_KEY));
  const [loading, setLoading] = useState(true);

  // Validate the stored token against /me on mount.
  //
  // Timeout (2026-08-06): plain fetch() has no default timeout. If the
  // backend accepts the connection but never responds (hung process, not a
  // clean connection-refused), this used to hang forever -- and because
  // this useEffect gates `loading` here, which gates the entire app shell
  // in App.jsx (nothing renders, not even the Sidebar, until this
  // resolves), a hung backend blocked the WHOLE app on the boot spinner
  // indefinitely, not just one page. Same root cause as api/client.js's
  // apiFetch() fix -- see that file's comment for the full story. A timeout
  // here is more important than almost anywhere else in the app for
  // exactly that reason.
  useEffect(() => {
    if (!token) { setLoading(false); return; }
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    fetch('/api/v1/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
      signal:  controller.signal,
    })
      .then(r => {
        // Only a genuine rejection from the server (401/403 = token really
        // is invalid) should log the user out. A timeout/network failure
        // means we couldn't verify the token, not that it's bad -- clearing
        // it here would force a re-login on every transient backend hiccup.
        // Keep the existing token and let the next /me check (or any
        // authenticated request) re-validate it later.
        if (!r.ok && (r.status === 401 || r.status === 403)) {
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
        }
      })
      .catch(() => { /* network error or timeout -- fail open, keep the token */ })
      .finally(() => { clearTimeout(timeoutId); setLoading(false); });
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const login = useCallback(async (email, password) => {
    const res = await fetch('/api/v1/auth/login', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Invalid credentials');
    }
    const { access_token } = await res.json();
    localStorage.setItem(TOKEN_KEY, access_token);
    setToken(access_token);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
  }, []);

  return (
    <AuthContext.Provider value={{ token, login, logout, loading, isAuthenticated: !!token }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
