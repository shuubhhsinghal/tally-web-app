'use client';

import React, { useState, useEffect } from 'react';
import { useTheme } from 'next-themes';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Wifi, WifiOff, Layers, Settings as SettingsIcon, Moon, LogOut, ChevronRight, RefreshCw } from 'lucide-react';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { useSyncStatus } from '@/context/SyncStatusContext';

// The account sheet (avatar tap) replaces what used to be three separate
// header icons (manual sync, theme toggle, logout). Manual sync also still
// lives on the Queue page's own Sync Now action -- this is a second,
// quicker entry point to the same shared triggerManualSync, for whenever
// you just want to force a sync without navigating to Queue first.
export default function TopBar({ title, kicker, showBack = false, onBack, rightContent }) {
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const { showConfirmDialog, showToast } = useUI();
  const { user, logout } = useAuth();
  const { isOnline, isSyncing, triggerManualSync } = useSyncStatus();

  const handleSyncNow = async () => {
    const result = await triggerManualSync();
    if (result) {
      showToast('Synced with Tally');
    } else if (isOnline !== false) {
      showToast('Sync failed -- try again', 'error');
    }
  };
  const [mounted, setMounted] = useState(false);
  const [acctOpen, setAcctOpen] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
  }, []);

  // Matches the design system's connection indicator: calm/neutral while
  // connected, accent (the one "needs attention" color) while offline or
  // still connecting -- not a semantic red/green/yellow traffic light.
  const connNeutral = isOnline === true;
  const initial = (user?.name || '?').charAt(0).toUpperCase();

  const goTo = (path) => { setAcctOpen(false); router.push(path); };

  return (
    <>
      <div className="sticky top-0 z-30 bg-bg/90 backdrop-blur-md border-b border-divider">
        <div className="flex items-center gap-3 h-16 px-4 max-w-md mx-auto">

          {showBack ? (
            <button
              onClick={() => onBack ? onBack() : router.back()}
              className="w-10 h-10 -ml-1 flex items-center justify-center rounded-full text-text hover:bg-text/6 transition-colors shrink-0"
            >
              <ArrowLeft className="w-6 h-6" />
            </button>
          ) : (
            <button
              onClick={() => setAcctOpen(true)}
              aria-label="Account, masters and settings"
              className="w-10 h-10 flex items-center justify-center rounded-full border border-accent text-accent-700 font-heading font-semibold text-[17px] hover:bg-accent/10 transition-colors shrink-0"
            >
              {initial}
            </button>
          )}

          <div className="flex-1 min-w-0">
            {kicker && (
              <div className="text-[10.5px] tracking-[0.12em] uppercase text-neutral-700 truncate">{kicker}</div>
            )}
            <h1 className="font-heading font-semibold text-[22px] leading-tight truncate">{title}</h1>
          </div>

          {rightContent ? rightContent : (
            <div className={`flex items-center gap-1.5 h-9 px-3 rounded-full border shrink-0 ${connNeutral ? 'border-divider text-neutral-700' : 'border-accent text-accent-700'}`}>
              {isOnline === false ? <WifiOff className="w-3.5 h-3.5" /> : <Wifi className="w-3.5 h-3.5" />}
              <span className="text-xs font-medium whitespace-nowrap">
                {isOnline === null ? 'Connecting…' : isOnline ? 'Online' : 'Offline'}
              </span>
            </div>
          )}

        </div>
      </div>

      {/* Account sheet */}
      {mounted && acctOpen && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={() => setAcctOpen(false)} />
          <div className="fixed bottom-0 left-0 right-0 z-50 bg-surface border-t border-divider rounded-t-lg shadow-lg pb-safe animate-in slide-in-from-bottom-full">
            <div className="flex items-center gap-3.5 px-5 py-4 border-b border-divider">
              <span className="w-12 h-12 rounded-full border border-accent text-accent-700 font-heading font-semibold text-xl flex items-center justify-center shrink-0">
                {initial}
              </span>
              <div className="min-w-0">
                <div className="font-heading font-semibold text-lg truncate">{user?.name}</div>
                <div className="text-sm text-neutral-700 truncate">
                  {user?.is_owner ? 'Owner' : 'Staff'}{user?.store_name ? ` · ${user.store_name}` : ''}
                </div>
              </div>
            </div>

            <button
              onClick={handleSyncNow}
              disabled={isSyncing || isOnline === false}
              className="w-full flex items-center gap-3.5 px-5 py-4 border-b border-divider hover:bg-text/4 transition-colors text-left disabled:opacity-60"
            >
              <RefreshCw className={`w-5 h-5 text-neutral-700 shrink-0 ${isSyncing ? 'animate-spin' : ''}`} />
              <div className="flex-1 min-w-0">
                <div className="text-[15px]">Sync now</div>
                <div className="text-xs text-neutral-600">
                  {isOnline === false ? 'Waiting for internet' : isSyncing ? 'Syncing with Tally…' : 'Post queued entries and refresh masters'}
                </div>
              </div>
            </button>

            <button
              onClick={() => goTo('/masters')}
              className="w-full flex items-center gap-3.5 px-5 py-4 border-b border-divider hover:bg-text/4 transition-colors text-left"
            >
              <Layers className="w-5 h-5 text-neutral-700 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-[15px]">Masters overview</div>
                <div className="text-xs text-neutral-600">Ledgers, items and cost centres from Tally</div>
              </div>
              <ChevronRight className="w-4 h-4 text-neutral-500 shrink-0" />
            </button>

            <button
              onClick={() => goTo('/settings')}
              className="w-full flex items-center gap-3.5 px-5 py-4 border-b border-divider hover:bg-text/4 transition-colors text-left"
            >
              <SettingsIcon className="w-5 h-5 text-neutral-700 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-[15px]">Settings</div>
                <div className="text-xs text-neutral-600">Password, AI item matching and team</div>
              </div>
              <ChevronRight className="w-4 h-4 text-neutral-500 shrink-0" />
            </button>

            <div className="flex items-center gap-3.5 px-5 py-4 border-b border-divider">
              <Moon className="w-5 h-5 text-neutral-700 shrink-0" />
              <div className="flex-1 text-[15px]">Appearance</div>
              <div className="flex border border-divider rounded-md p-0.5 gap-0.5">
                <button
                  onClick={() => setTheme('light')}
                  className={`px-3.5 h-8 text-sm rounded-[3px] transition-colors ${theme === 'light' ? 'border border-accent text-accent-700' : 'text-neutral-700 hover:bg-text/5'}`}
                >
                  Light
                </button>
                <button
                  onClick={() => setTheme('dark')}
                  className={`px-3.5 h-8 text-sm rounded-[3px] transition-colors ${theme === 'dark' ? 'border border-accent text-accent-700' : 'text-neutral-700 hover:bg-text/5'}`}
                >
                  Dark
                </button>
              </div>
            </div>

            <button
              onClick={() => { setAcctOpen(false); showConfirmDialog({ title: 'Log out?', onConfirm: logout }); }}
              className="w-full flex items-center gap-3.5 px-5 py-4 text-accent-700 hover:bg-text/4 transition-colors text-left"
            >
              <LogOut className="w-5 h-5 shrink-0" />
              <span className="text-[15px]">Log out</span>
            </button>
          </div>
        </>
      )}
    </>
  );
}
