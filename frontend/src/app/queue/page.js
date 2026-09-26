'use client';

import { useState, useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import TopBar from '@/components/layout/TopBar';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { EmptyState } from '@/components/ui/EmptyState';
import { ChevronLeft, ChevronRight, Wifi, WifiOff, RefreshCw } from 'lucide-react';
import { ActivityRow } from '@/components/activity/ActivityRow';
import { TransactionDetailView } from '@/components/activity/TransactionDetailView';
import { useUI } from '@/context/UIContext';
import { useSyncStatus } from '@/context/SyncStatusContext';

const LIMIT = 10;
const TABS = [
  { id: 'PENDING', label: 'Pending' },
  { id: 'FAILED', label: 'Failed' },
  { id: 'SYNCED', label: 'Synced' },
];

// Fixed set -- these are the app's own hardcoded transaction-recording features,
// not data from a mutable database table, so hardcoding them here is appropriate
// (same reasoning as the status tabs above). Master-creation rows intentionally
// have no entry here; they still show under "All Types".
const TYPE_OPTIONS = [
  { id: '', label: 'All types' },
  { id: 'SALES', label: 'Sales' },
  { id: 'PURCHASE', label: 'Purchase' },
  { id: 'PURCHASE_ITEM', label: 'Purchase (item-wise)' },
  { id: 'PAYMENT', label: 'Payment' },
  { id: 'TRANSFER', label: 'Transfer' },
  { id: 'STOCK_TRANSFER', label: 'Stock transfer' },
  { id: 'BANK_STATEMENT', label: 'Bank statement' },
  { id: 'REPACK', label: 'Repack' },
];

function formatSyncTime(iso) {
  if (!iso) return null;
  const d = new Date(iso.includes('T') || iso.includes('Z') ? iso : iso.replace(' ', 'T') + 'Z');
  if (isNaN(d.getTime())) return null;
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function QueueContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { showToast, showConfirmDialog } = useUI();
  const { isOnline, isSyncing, lastSyncedAt, triggerManualSync } = useSyncStatus();

  const status = searchParams.get('status') || 'PENDING';
  const store = searchParams.get('store') || 'All';
  const type = searchParams.get('type') || '';
  const page = Number(searchParams.get('page')) || 1;

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedItemId, setSelectedItemId] = useState(null);
  const [storeNames, setStoreNames] = useState([]);
  const [bulkActing, setBulkActing] = useState(false);
  const [counts, setCounts] = useState({ PENDING: 0, FAILED: 0, SYNCED: 0 });

  useEffect(() => {
    fetch('/api/settings/stores')
      .then(res => res.ok ? res.json() : [])
      .then(stores => setStoreNames(stores.map(s => s.store_name)))
      .catch(() => {});
  }, []);

  const filterParams = (forStore, forType) => {
    const storeParam = forStore !== 'All' ? `&store=${encodeURIComponent(forStore)}` : '';
    const typeParam = forType ? `&type=${encodeURIComponent(forType)}` : '';
    return `${storeParam}${typeParam}`;
  };

  const fetchCounts = async () => {
    try {
      const results = await Promise.all(
        ['PENDING', 'FAILED', 'SYNCED'].map(st =>
          fetch(`/api/dashboard/queue?status=${st}&limit=1`).then(res => res.ok ? res.json() : { total: 0 }).catch(() => ({ total: 0 }))
        )
      );
      setCounts({ PENDING: results[0].total || 0, FAILED: results[1].total || 0, SYNCED: results[2].total || 0 });
    } catch { }
  };

  const fetchQueue = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/dashboard/queue?status=${status}${filterParams(store, type)}&page=${page}&limit=${LIMIT}`);
      if (res.ok) {
        const data = await res.json();
        setItems(data.items);
        setTotal(data.total);
      }
    } catch (e) {
      console.error("Failed to load queue", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchQueue();
    fetchCounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, store, type, page]);

  const navigate = (newStatus, newStore, newType, newPage) => {
    router.replace(`/queue?status=${newStatus}${filterParams(newStore, newType)}&page=${newPage}`);
  };

  const setTab = (newStatus) => navigate(newStatus, store, type, 1);
  const setStoreFilter = (newStore) => navigate(status, newStore, type, 1);
  const setTypeFilter = (newType) => navigate(status, store, newType, 1);

  const handleSyncNow = async () => {
    await triggerManualSync();
    fetchQueue();
    fetchCounts();
  };

  const handleClearAllFailed = () => {
    showConfirmDialog({
      title: "Clear all failed?",
      message: `This permanently deletes all ${total} failed item${total === 1 ? '' : 's'} currently shown (items with unconfirmed Tally delivery are skipped). This can't be undone.`,
      danger: true,
      onConfirm: async () => {
        setBulkActing(true);
        try {
          const res = await fetch(`/api/dashboard/queue/clear-failed?${filterParams(store, type).slice(1)}`, { method: 'POST' });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(body.detail || "Failed to clear items");
          if (body.skipped > 0) {
            showToast(`Cleared ${body.cleared}. Skipped ${body.skipped} with unconfirmed Tally delivery -- verify those manually.`);
          } else {
            showToast(body.message || "Cleared");
          }
          fetchQueue();
          fetchCounts();
        } catch (e) {
          showToast(e.message || "Failed to clear items", "error");
        } finally {
          setBulkActing(false);
        }
      }
    });
  };

  const handleRetryAllFailed = async () => {
    setBulkActing(true);
    try {
      const res = await fetch(`/api/dashboard/queue/retry-failed?${filterParams(store, type).slice(1)}`, { method: 'POST' });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "Failed to retry items");
      if (body.skipped_uncertain > 0) {
        showToast(`Retrying ${body.retried}. Skipped ${body.skipped_uncertain} with unconfirmed Tally delivery -- verify those manually.`);
      } else {
        showToast(body.message || "Retrying");
      }
      fetchQueue();
      fetchCounts();
    } catch (e) {
      showToast(e.message || "Failed to retry items", "error");
    } finally {
      setBulkActing(false);
    }
  };
  const goToPage = (newPage) => navigate(status, store, type, newPage);

  const totalPages = Math.ceil(total / LIMIT);
  const offsetStart = (page - 1) * LIMIT + 1;
  const storePills = ['All', ...storeNames, 'Unallocated'];
  const syncTime = formatSyncTime(lastSyncedAt);
  const pendingCount = counts.PENDING;

  return (
    <div className="min-h-screen bg-bg pb-24">
      <TopBar
        title="Queue"
        kicker="TALLY SYNC"
        rightContent={
          <div className={`flex items-center gap-1.5 h-9 px-3 rounded-full border shrink-0 ${isOnline === false ? 'border-accent text-accent-700' : 'border-divider text-neutral-700'}`}>
            {isOnline === false ? <WifiOff className="w-3.5 h-3.5" /> : <Wifi className="w-3.5 h-3.5" />}
            <span className="text-xs font-medium whitespace-nowrap">
              {isOnline === false ? 'Offline' : 'Online'}{pendingCount > 0 ? ` · ${pendingCount} saved` : ''}
            </span>
          </div>
        }
      />

      <div className="max-w-md mx-auto px-4 pt-6 space-y-4">
        <div className="flex items-start gap-3">
          <div className={`w-12 h-12 rounded-full border flex items-center justify-center shrink-0 ${isOnline === false ? 'border-accent text-accent-700' : 'border-divider text-neutral-700'}`}>
            {isOnline === false ? <WifiOff className="w-5 h-5" /> : <Wifi className="w-5 h-5" />}
          </div>
          <div className="min-w-0">
            <h2 className="font-heading font-semibold text-xl leading-tight">
              {isOnline === false
                ? `${pendingCount} entr${pendingCount === 1 ? 'y' : 'ies'} on this phone`
                : pendingCount > 0
                  ? `${pendingCount} entr${pendingCount === 1 ? 'y' : 'ies'} syncing to Tally`
                  : 'All synced to Tally'}
            </h2>
            <p className="text-[13px] text-neutral-700 mt-1">
              {isOnline === false
                ? "They're safe here and will be sent automatically when the internet is back. Keep recording as usual."
                : pendingCount > 0
                  ? "They'll clear automatically -- you can also sync now."
                  : 'Nothing is waiting to be sent right now.'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          {isOnline === false ? (
            <span className="inline-flex items-center gap-2 border border-accent text-accent-700 rounded-md px-3.5 h-10 text-sm opacity-70">
              <RefreshCw className="w-4 h-4" /> Waiting for internet
            </span>
          ) : (
            <button
              onClick={handleSyncNow}
              disabled={isSyncing}
              className="inline-flex items-center gap-2 border border-accent text-accent-700 rounded-md px-3.5 h-10 text-sm hover:bg-accent/8 transition-colors disabled:opacity-60"
            >
              <RefreshCw className={`w-4 h-4 ${isSyncing ? 'animate-spin' : ''}`} /> Sync now
            </button>
          )}
          {syncTime && <span className="text-[13px] text-neutral-700">Last posted to Tally at {syncTime}</span>}
        </div>

        <div className="border-t border-divider" />

        <div className="grid grid-cols-3 gap-2">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setTab(tab.id)}
              className={`h-11 rounded-md border text-sm transition-colors ${status === tab.id ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider text-text hover:border-text/40'}`}
            >
              {tab.label} · {counts[tab.id]}
            </button>
          ))}
        </div>

        <div className="flex gap-2 overflow-x-auto -mx-1 px-1" style={{ scrollbarWidth: 'none' }}>
          {storePills.map(name => (
            <button
              key={name}
              onClick={() => setStoreFilter(name)}
              className={`shrink-0 px-3.5 h-9 rounded-full border text-sm whitespace-nowrap transition-colors ${store === name ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider text-text hover:border-text/45'}`}
            >
              {name}
            </button>
          ))}
        </div>

        <Select value={type} onChange={e => setTypeFilter(e.target.value)}>
          {TYPE_OPTIONS.map(opt => (
            <option key={opt.id} value={opt.id}>{opt.label}</option>
          ))}
        </Select>

        {status === 'FAILED' && total > 0 && (
          <div className="flex gap-2">
            <Button
              variant="secondary"
              className="flex-1"
              onClick={handleRetryAllFailed}
              disabled={bulkActing}
            >
              Retry all
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              onClick={handleClearAllFailed}
              disabled={bulkActing}
            >
              Clear all
            </Button>
          </div>
        )}

        {loading && items.length === 0 ? (
          <p className="text-sm text-neutral-600 text-center py-16">Loading&hellip;</p>
        ) : items.length === 0 ? (
          <EmptyState
            title={`No ${status.toLowerCase()} transactions`}
            message="Nothing to show here right now."
          />
        ) : (
          <div>
            {items.map((activity) => (
              <ActivityRow
                key={activity.id}
                activity={activity}
                onClick={() => setSelectedItemId(activity.id)}
              />
            ))}
          </div>
        )}

        {total > 0 && (
          <div className="flex items-center justify-between pt-2">
            <span className="text-[13px] text-neutral-700">
              Showing {items.length > 0 ? offsetStart : 0}&ndash;{Math.min(page * LIMIT, total)} of {total}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => goToPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="w-9 h-9 flex items-center justify-center rounded-md border border-divider text-neutral-700 disabled:opacity-40 hover:border-text/40 transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-[13px] text-neutral-700 whitespace-nowrap">Page {page} of {totalPages || 1}</span>
              <button
                onClick={() => goToPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages || totalPages === 0}
                className="w-9 h-9 flex items-center justify-center rounded-md border border-divider text-neutral-700 disabled:opacity-40 hover:border-text/40 transition-colors"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      {selectedItemId && (
        <TransactionDetailView
          itemId={selectedItemId}
          onClose={() => setSelectedItemId(null)}
          onMutated={() => { fetchQueue(); fetchCounts(); }}
        />
      )}
    </div>
  );
}

export default function QueuePage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-bg pb-24">
        <p className="text-sm text-neutral-600 text-center py-16">Loading queue&hellip;</p>
      </div>
    }>
      <QueueContent />
    </Suspense>
  );
}
