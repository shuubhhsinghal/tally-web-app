'use client';

import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { fetchWithTimeout } from '@/utils/fetchWithTimeout';

const SyncStatusContext = createContext();
export const useSyncStatus = () => useContext(SyncStatusContext);

// Lives in the root Providers tree (mounted once for the whole app session)
// rather than inside TopBar itself -- TopBar is rendered fresh on every
// page (each page passes its own title/back-button props), so polling from
// there re-fired the status check and restarted the 10s interval on every
// single navigation. Hoisting the poll here means it starts once and just
// keeps running underneath page changes.
export function SyncStatusProvider({ children }) {
  const [isOnline, setIsOnline] = useState(null);
  const [isSyncing, setIsSyncing] = useState(false);

  useEffect(() => {
    const checkSync = async () => {
      try {
        // Short timeout -- this reruns every 10s anyway, so there's no
        // point letting one attempt hang longer than that before showing
        // "offline" and letting the next tick retry.
        const res = await fetchWithTimeout('/api/sync/status', {}, 8000);
        if (res.ok) {
          const data = await res.json();
          setIsOnline(data.online !== false);
        } else {
          setIsOnline(false);
        }
      } catch {
        setIsOnline(false);
      }
    };
    checkSync();
    const interval = setInterval(checkSync, 10000);
    return () => clearInterval(interval);
  }, []);

  const triggerManualSync = useCallback(async () => {
    setIsSyncing(true);
    try {
      // More generous timeout than the status check -- this one actually
      // does real Tally I/O (posting queued vouchers, pulling reporting
      // data), which can legitimately take a while.
      const res = await fetchWithTimeout('/api/sync/sync-queue', { method: 'POST' }, 30000);
      if (res.ok) {
        return await res.json();
      }
      return null;
    } finally {
      setIsSyncing(false);
    }
  }, []);

  return (
    <SyncStatusContext.Provider value={{ isOnline, isSyncing, triggerManualSync }}>
      {children}
    </SyncStatusContext.Provider>
  );
}
