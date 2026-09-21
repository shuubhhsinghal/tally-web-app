'use client';

import { createContext, useContext, useState, useEffect, useCallback } from 'react';

const AuthContext = createContext();
export const useAuth = () => useContext(AuthContext);

const TOKEN_KEY = 'auth_token';

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // 'loading' | 'authed' | 'guest' | 'needs_setup'
  const [status, setStatus] = useState('loading');

  useEffect(() => {
    // Attach the session token to every /api/* call, once, instead of
    // editing every one of this app's many existing fetch() call sites.
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (url.startsWith('/api/')) {
        const token = localStorage.getItem(TOKEN_KEY);
        if (token) {
          init = { ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` } };
        }
      }
      return originalFetch(input, init);
    };
    return () => { window.fetch = originalFetch; };
  }, []);

  const checkAuth = useCallback(async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      try {
        const res = await fetch('/api/auth/needs-setup');
        const data = await res.json();
        setStatus(data.needs_setup ? 'needs_setup' : 'guest');
      } catch {
        setStatus('guest');
      }
      return;
    }

    try {
      const res = await fetch('/api/auth/me');
      if (res.ok) {
        setUser(await res.json());
        setStatus('authed');
      } else {
        localStorage.removeItem(TOKEN_KEY);
        setStatus('guest');
      }
    } catch {
      setStatus('guest');
    }
  }, []);

  useEffect(() => { checkAuth(); }, [checkAuth]);

  const login = (token, userData) => {
    localStorage.setItem(TOKEN_KEY, token);
    setUser(userData);
    setStatus('authed');
  };

  const logout = async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch { }
    localStorage.removeItem(TOKEN_KEY);
    setUser(null);
    setStatus('guest');
  };

  return (
    <AuthContext.Provider value={{ user, status, login, logout, refresh: checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
}
