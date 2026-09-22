'use client';

import React, { useState, useEffect } from 'react';
import { useTheme } from 'next-themes';
import { useRouter } from 'next/navigation';
import { Moon, Sun, ArrowLeft, Wifi, WifiOff, RefreshCw, LogOut } from 'lucide-react';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { fetchWithTimeout } from '@/utils/fetchWithTimeout';

export default function TopBar({ title, showBack = false, onBack }) {
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const { showToast, showConfirmDialog } = useUI();
  const { user, logout } = useAuth();
  const [mounted, setMounted] = useState(false);
  const [isOnline, setIsOnline] = useState(null);
  const [isSyncing, setIsSyncing] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
    // Simple mock ping to check Tally sync status based on user instruction
    const checkSync = async () => {
      try {
        // Short timeout -- this reruns every 10s anyway (see the interval
        // below), so there's no point letting one attempt hang longer than
        // that before showing "offline" and letting the next tick retry.
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

  const handleManualSync = async () => {
    setIsSyncing(true);
    try {
      // More generous timeout than the status check -- this one actually
      // does real Tally I/O (posting queued vouchers, pulling reporting
      // data), which can legitimately take a while.
      const res = await fetchWithTimeout('/api/sync/sync-queue', { method: 'POST' }, 30000);
      if (res.ok) {
        const data = await res.json();
        const hasIssue = data.status !== 'success' || (data.failed_months && data.failed_months.length > 0);
        showToast(data.message || 'Manual sync triggered', hasIssue ? 'error' : 'success');
      } else {
        showToast('Failed to trigger manual sync', 'error');
      }
    } catch (e) {
      showToast(e.message || 'Error triggering sync', 'error');
    } finally {
      setIsSyncing(false);
    }
  };

  return (
    <div className="sticky top-0 z-30 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b border-gray-100 dark:border-gray-800">
      <div className="flex items-center justify-between h-14 px-4 max-w-md mx-auto">
        
        {/* Left Section -- sized to its own content (a lone back button is
            much narrower than the status block shown when there's no back
            button) rather than flex-1, so it doesn't claim a full third of
            the header and starve the title next to it. */}
        <div className="flex items-center justify-start">
          {showBack ? (
            <button 
              onClick={() => onBack ? onBack() : router.back()} 
              className="p-2 -ml-2 text-gray-900 dark:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-full transition-colors"
            >
              <ArrowLeft className="w-6 h-6" />
            </button>
          ) : (
            <div className="flex flex-col">
              <div className="flex items-center gap-2">
                <div className={`w-2.5 h-2.5 rounded-full ${isOnline === null ? 'bg-yellow-500 animate-pulse' : isOnline ? 'bg-green-500' : 'bg-red-500'}`} />
                <span className="text-xs font-semibold text-gray-500 dark:text-gray-400">
                  {isOnline === null ? 'Connecting...' : isOnline ? 'Tally connected' : 'Tally offline'}
                </span>
              </div>
              {user && (
                <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-4 truncate max-w-[90px]">
                  {user.name}{!user.is_owner && user.store_name ? ` · ${user.store_name}` : ''}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Center Title */}
        <h1 className="text-lg font-bold text-gray-900 dark:text-white truncate px-2 text-center flex-1">
          {title}
        </h1>

        {/* Right Section -- same reasoning as the left one above. */}
        <div className="flex items-center justify-end gap-1">
          <button
            onClick={handleManualSync}
            disabled={isSyncing}
            className="p-2 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-full transition-colors disabled:opacity-50"
            aria-label="Manual Sync"
          >
            <RefreshCw className={`w-5 h-5 ${isSyncing ? 'animate-spin' : ''}`} />
          </button>
          {mounted && (
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="p-2 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-full transition-colors"
              aria-label="Toggle Theme"
            >
              {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            </button>
          )}
          <button
            onClick={() => showConfirmDialog({ title: 'Log out?', onConfirm: logout })}
            className="p-2 -mr-2 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-full transition-colors"
            aria-label="Log out"
          >
            <LogOut className="w-5 h-5" />
          </button>
        </div>

      </div>
    </div>
  );
}
