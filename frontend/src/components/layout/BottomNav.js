'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  Home, Plus, Landmark, RefreshCw, Receipt, ShoppingCart, ArrowDownLeft, ArrowUpRight,
  Package, ArrowLeftRight, Layers, RotateCcw, Inbox, BarChart3, X, ClipboardList
} from 'lucide-react';
import { useUI } from '@/context/UIContext';
import { useSyncStatus } from '@/context/SyncStatusContext';

// Matches the "New entry" design.
const ENTRY_TILES = [
  { label: 'Sale', sub: 'Sales voucher', icon: Receipt, href: '/sales' },
  { label: 'Purchase', sub: 'Purchase voucher', icon: ShoppingCart, href: '/purchase' },
  { label: 'Journal', sub: 'Journal voucher', icon: ArrowDownLeft, href: '/journal' },
  { label: 'Payment', sub: 'Payment voucher', icon: ArrowUpRight, href: '/payment' },
  { label: 'Stock transfer', sub: 'Stock journal voucher', icon: Package, href: '/stock-transfer' },
  { label: 'Transfer', sub: 'Contra voucher', icon: ArrowLeftRight, href: '/transfer' },
  { label: 'Repack', sub: 'Stock journal voucher', icon: Layers, href: '/repack' },
  // Same Supplier Adjustment/Return (Debit Note) as the "Return" tab nested
  // inside Item-wise Purchase -- this tile just deep-links straight to it
  // instead of requiring a purchase entry first.
  { label: 'Purchase return', sub: 'Debit note voucher', icon: RotateCcw, href: '/purchase?mode=return' },
  { label: 'Stock count', sub: 'Count & value inventory', icon: ClipboardList, href: '/stock-count' },
  // Owner-only -- a staff account tapping this still lands safely on the
  // Loans page's own restricted-access message rather than needing to be
  // filtered out of this list.
  { label: 'Loans', sub: 'Track lenders & interest', icon: Landmark, href: '/loans' },
];

function NewEntrySheet({ onClose }) {
  const router = useRouter();
  const { showToast } = useUI();
  const { isOnline } = useSyncStatus();

  const handleTap = (tile) => {
    onClose();
    if (tile.href) router.push(tile.href);
    else showToast(`${tile.label} isn't set up yet -- coming soon.`, 'info');
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-x-0 bottom-0 z-50 bg-bg border-t border-divider rounded-t-lg shadow-lg pb-safe">
        <div className="flex justify-center pt-2.5"><div className="w-9 h-1 rounded-full bg-divider" /></div>
        <div className="flex items-start justify-between px-5 pt-3 pb-4">
          <div>
            <h2 className="font-heading font-semibold text-2xl">New entry</h2>
            <p className="text-[12.5px] text-neutral-700 mt-0.5">
              {isOnline === false ? 'Offline — saved on this phone until you reconnect' : 'Synced to Tally right away'}
            </p>
          </div>
          <button onClick={onClose} className="p-1 text-neutral-600 hover:text-text" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="grid grid-cols-2 gap-2 px-5 pb-6">
          {ENTRY_TILES.map(tile => (
            <button
              key={tile.label}
              onClick={() => handleTap(tile)}
              className="flex items-center gap-3 min-h-[66px] px-3 py-2.5 border border-divider rounded-md text-left hover:border-accent active:bg-accent/12 transition-colors"
            >
              <tile.icon className="w-5 h-5 text-accent-700 shrink-0" />
              <span className="min-w-0">
                <span className="block font-heading font-semibold text-[17px] leading-tight">{tile.label}</span>
                <span className="block text-[11px] text-neutral-700 mt-0.5">{tile.sub}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

function TabBadge({ count }) {
  if (!count) return null;
  return (
    <span className="absolute -top-1.5 left-1/2 translate-x-2 min-w-[17px] h-[17px] px-1 rounded-full border border-accent bg-bg text-accent-700 text-[10px] leading-[15px] text-center [font-feature-settings:'tnum']">
      {count}
    </span>
  );
}

export default function BottomNav() {
  const pathname = usePathname();
  const [entryOpen, setEntryOpen] = useState(false);
  const [badges, setBadges] = useState({ queue: 0, review: 0 });

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetch('/api/dashboard/stats')
        .then(res => res.ok ? res.json() : null)
        .then(data => { if (data && !cancelled) setBadges({ queue: data.queue_count || 0, review: data.review_count || 0 }); })
        .catch(() => {});
    };
    poll();
    const interval = setInterval(poll, 10000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  // Highlight 'home' if on dashboard, 'bank' if on bank-statement, 'queue' if on queue
  const isHome = pathname === '/dashboard' || pathname === '/';
  const isBank = pathname.startsWith('/bank-statement');
  const isReview = pathname.startsWith('/review');
  const isReports = pathname.startsWith('/reporting');
  const isQueue = pathname.startsWith('/queue');

  return (
    <>
      {/* Spacer to prevent content from hiding behind the fixed nav */}
      <div className="h-24" />

      {/* Plus Button -- floats independently above the tab bar */}
      <button
        onClick={() => setEntryOpen(true)}
        aria-label="New entry"
        className="fixed left-1/2 -translate-x-1/2 bottom-[calc(76px+env(safe-area-inset-bottom))] z-40 w-[52px] h-[52px] bg-bg border border-accent hover:bg-accent/12 active:scale-95 text-accent rounded-full flex items-center justify-center shadow-md transition-all"
      >
        <Plus className="w-7 h-7" />
      </button>

      <div className="fixed bottom-0 left-0 right-0 z-30 bg-bg/90 backdrop-blur-md border-t border-divider pb-safe">
        <div className="max-w-md mx-auto grid grid-cols-5">

          <Link href="/dashboard" className="flex flex-col items-center justify-center h-16 gap-1">
            <Home className={`w-6 h-6 ${isHome ? 'text-accent-700' : 'text-neutral-500'}`} />
            <span className={`text-[10px] font-medium ${isHome ? 'text-accent-700' : 'text-neutral-500'}`}>Home</span>
          </Link>

          <Link href="/bank-statement" className="flex flex-col items-center justify-center h-16 gap-1">
            <Landmark className={`w-6 h-6 ${isBank ? 'text-accent-700' : 'text-neutral-500'}`} />
            <span className={`text-[10px] font-medium ${isBank ? 'text-accent-700' : 'text-neutral-500'}`}>Bank</span>
          </Link>

          <Link href="/reporting/sales" className="flex flex-col items-center justify-center h-16 gap-1">
            <BarChart3 className={`w-6 h-6 ${isReports ? 'text-accent-700' : 'text-neutral-500'}`} />
            <span className={`text-[10px] font-medium ${isReports ? 'text-accent-700' : 'text-neutral-500'}`}>Reports</span>
          </Link>

          <Link href="/review" className="relative flex flex-col items-center justify-center h-16 gap-1">
            <span className="relative">
              <Inbox className={`w-6 h-6 ${isReview ? 'text-accent-700' : 'text-neutral-500'}`} />
              <TabBadge count={badges.review} />
            </span>
            <span className={`text-[10px] font-medium ${isReview ? 'text-accent-700' : 'text-neutral-500'}`}>Review</span>
          </Link>

          <Link href="/queue" className="relative flex flex-col items-center justify-center h-16 gap-1">
            <span className="relative">
              <RefreshCw className={`w-6 h-6 ${isQueue ? 'text-accent-700' : 'text-neutral-500'}`} />
              <TabBadge count={badges.queue} />
            </span>
            <span className={`text-[10px] font-medium ${isQueue ? 'text-accent-700' : 'text-neutral-500'}`}>Queue</span>
          </Link>

        </div>
      </div>

      {entryOpen && <NewEntrySheet onClose={() => setEntryOpen(false)} />}
    </>
  );
}
