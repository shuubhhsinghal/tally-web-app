'use client';

import React, { useState, useEffect } from 'react';
import { useTheme } from 'next-themes';
import { useRouter } from 'next/navigation';
import { Moon, Sun, ArrowLeft, Wifi, WifiOff } from 'lucide-react';

export default function TopBar({ title, showBack = false, onBack }) {
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  const [isOnline, setIsOnline] = useState(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
    // Simple mock ping to check Tally sync status based on user instruction
    const checkSync = async () => {
      try {
        const res = await fetch('/api/sync/status');
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

  return (
    <div className="sticky top-0 z-30 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b border-gray-100 dark:border-gray-800">
      <div className="flex items-center justify-between h-14 px-4 max-w-md mx-auto">
        
        {/* Left Section */}
        <div className="flex-1 flex items-center justify-start">
          {showBack ? (
            <button 
              onClick={() => onBack ? onBack() : router.back()} 
              className="p-2 -ml-2 text-gray-900 dark:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-full transition-colors"
            >
              <ArrowLeft className="w-6 h-6" />
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <div className={`w-2.5 h-2.5 rounded-full ${isOnline === null ? 'bg-yellow-500 animate-pulse' : isOnline ? 'bg-green-500' : 'bg-red-500'}`} />
              <span className="text-xs font-semibold text-gray-500 dark:text-gray-400">
                {isOnline === null ? 'Connecting...' : isOnline ? 'Tally connected' : 'Tally offline'}
              </span>
            </div>
          )}
        </div>

        {/* Center Title */}
        <h1 className="text-lg font-bold text-gray-900 dark:text-white truncate px-2 text-center flex-1">
          {title}
        </h1>

        {/* Right Section */}
        <div className="flex-1 flex items-center justify-end">
          {mounted && (
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="p-2 -mr-2 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-full transition-colors"
              aria-label="Toggle Theme"
            >
              {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            </button>
          )}
        </div>

      </div>
    </div>
  );
}
