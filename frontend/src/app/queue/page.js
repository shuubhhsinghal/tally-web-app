'use client';

import { useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import TopBar from '@/components/layout/TopBar';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { EmptyState } from '@/components/ui/EmptyState';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { ActivityRow } from '@/components/activity/ActivityRow';
import { TransactionDetailView } from '@/components/activity/TransactionDetailView';

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
  { id: '', label: 'All Types' },
  { id: 'SALES', label: 'Sales' },
  { id: 'PURCHASE', label: 'Purchase' },
  { id: 'PURCHASE_ITEM', label: 'Purchase (Item-wise)' },
  { id: 'PAYMENT', label: 'Payment' },
  { id: 'TRANSFER', label: 'Transfer' },
  { id: 'STOCK_TRANSFER', label: 'Stock Transfer' },
  { id: 'BANK_STATEMENT', label: 'Bank Statement' },
  { id: 'REPACK', label: 'Repack' },
];

export default function QueuePage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const status = searchParams.get('status') || 'PENDING';
  const store = searchParams.get('store') || 'All';
  const type = searchParams.get('type') || '';
  const page = Number(searchParams.get('page')) || 1;

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedItemId, setSelectedItemId] = useState(null);
  const [storeNames, setStoreNames] = useState([]);

  useEffect(() => {
    fetch('/api/settings/stores')
      .then(res => res.ok ? res.json() : [])
      .then(stores => setStoreNames(stores.map(s => s.store_name)))
      .catch(() => {});
  }, []);

  const fetchQueue = async () => {
    setLoading(true);
    try {
      const storeParam = store !== 'All' ? `&store=${encodeURIComponent(store)}` : '';
      const typeParam = type ? `&type=${encodeURIComponent(type)}` : '';
      const res = await fetch(`/api/dashboard/queue?status=${status}${storeParam}${typeParam}&page=${page}&limit=${LIMIT}`);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, store, type, page]);

  const navigate = (newStatus, newStore, newType, newPage) => {
    const storeParam = newStore !== 'All' ? `&store=${encodeURIComponent(newStore)}` : '';
    const typeParam = newType ? `&type=${encodeURIComponent(newType)}` : '';
    router.replace(`/queue?status=${newStatus}${storeParam}${typeParam}&page=${newPage}`);
  };

  const setTab = (newStatus) => navigate(newStatus, store, type, 1);
  const setStoreFilter = (newStore) => navigate(status, newStore, type, 1);
  const setTypeFilter = (newType) => navigate(status, store, newType, 1);
  const goToPage = (newPage) => navigate(status, store, type, newPage);

  if (selectedItemId) {
    return (
      <TransactionDetailView
        itemId={selectedItemId}
        onBack={() => setSelectedItemId(null)}
        onMutated={fetchQueue}
      />
    );
  }

  const totalPages = Math.ceil(total / LIMIT);
  const offsetStart = (page - 1) * LIMIT + 1;
  const storePills = ['All', ...storeNames, 'Unallocated'];

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar title="Queue" />

      <div className="max-w-md mx-auto p-4 space-y-4 mt-4">
        <div className="flex gap-2">
          {TABS.map(tab => (
            <Button
              key={tab.id}
              variant={status === tab.id ? 'primary' : 'secondary'}
              className="flex-1"
              onClick={() => setTab(tab.id)}
            >
              {tab.label}
            </Button>
          ))}
        </div>

        <div className="flex flex-wrap gap-2">
          {storePills.map(name => (
            <button
              key={name}
              onClick={() => setStoreFilter(name)}
              className={`px-3 py-1 text-xs font-bold rounded-full transition-colors ${store === name
                ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-700'
                }`}
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

        {loading && items.length === 0 ? (
          <div className="text-center py-12 text-sm font-medium text-gray-400">Loading...</div>
        ) : items.length === 0 ? (
          <EmptyState
            title={`No ${status.toLowerCase()} transactions`}
            message="Nothing to show here right now."
          />
        ) : (
          <div className="flex flex-col gap-3">
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
            <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
              Showing {items.length > 0 ? offsetStart : 0} to {Math.min(page * LIMIT, total)} of {total}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => goToPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="p-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-500 disabled:opacity-50 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-xs font-medium text-gray-700 dark:text-gray-300">Page {page} of {totalPages || 1}</span>
              <button
                onClick={() => goToPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages || totalPages === 0}
                className="p-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-500 disabled:opacity-50 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
