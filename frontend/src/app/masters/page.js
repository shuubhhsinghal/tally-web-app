'use client';
import { useState, useEffect } from 'react';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { AlertCircle, CheckCircle2, Search, RefreshCw, XCircle } from 'lucide-react';

function StatusBadge({ status }) {
  if (status === 'in_tally') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-green-600 bg-green-50 dark:bg-green-900/30 px-2 py-1 rounded whitespace-nowrap">
        <CheckCircle2 className="w-3 h-3" /> In Tally
      </span>
    );
  }
  if (status === 'in_queue') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-amber-600 bg-amber-50 dark:bg-amber-900/30 px-2 py-1 rounded whitespace-nowrap">
        <AlertCircle className="w-3 h-3" /> In Queue
      </span>
    );
  }
  if (status === 'syncing') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-blue-600 bg-blue-50 dark:bg-blue-900/30 px-2 py-1 rounded whitespace-nowrap">
        <RefreshCw className="w-3 h-3 animate-spin-slow" /> Syncing
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-red-600 bg-red-50 dark:bg-red-900/30 px-2 py-1 rounded whitespace-nowrap">
        <XCircle className="w-3 h-3" /> Failed
      </span>
    );
  }
  return null;
}

export default function MastersPage() {
  const [activeTab, setActiveTab] = useState('ledgers');
  const [data, setData] = useState({ ledgers: [], items: [], counts: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  const fetchMasters = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/masters");
      if (!res.ok) throw new Error("Failed to fetch masters");
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMasters();
  }, []);

  const normalizeStr = (str) => (str || "").replace(/\s+/g, ' ').trim().toLowerCase();

  // Filter ledgers
  const filteredLedgers = data.ledgers.filter(ledger => {
    const s = normalizeStr(search);
    const matchesSearch = s === "" || normalizeStr(ledger.name).includes(s) || normalizeStr(ledger.parent).includes(s);
    let matchesStatus = true;
    if (statusFilter === 'in_tally') matchesStatus = ledger.status === 'in_tally';
    if (statusFilter === 'in_queue') matchesStatus = ledger.status === 'in_queue' || ledger.status === 'syncing';
    if (statusFilter === 'failed') matchesStatus = ledger.status === 'failed';
    return matchesSearch && matchesStatus;
  });

  // Group ledgers by parent
  const groupedLedgers = {};
  filteredLedgers.forEach(l => {
    if (!groupedLedgers[l.parent]) groupedLedgers[l.parent] = [];
    groupedLedgers[l.parent].push(l);
  });

  const sortedGroups = Object.keys(groupedLedgers).sort((a, b) => a.localeCompare(b));
  sortedGroups.forEach(g => {
    groupedLedgers[g].sort((a, b) => a.name.localeCompare(b.name));
  });

  // Filter items
  const filteredItems = data.items.filter(item => {
    const s = normalizeStr(search);
    const matchesSearch = s === "" || normalizeStr(item.name).includes(s) || normalizeStr(item.unit).includes(s);
    let matchesStatus = true;
    if (statusFilter === 'in_tally') matchesStatus = item.status === 'in_tally';
    if (statusFilter === 'in_queue') matchesStatus = item.status === 'in_queue' || item.status === 'syncing';
    if (statusFilter === 'failed') matchesStatus = item.status === 'failed';
    return matchesSearch && matchesStatus;
  });

  // Sort items
  const sortedItems = [...filteredItems].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar
        title="Masters"
        rightAction={
          <button onClick={fetchMasters} className="p-2 -mr-2 text-gray-500 hover:text-teal-600 transition-colors">
            <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        }
      />

      <div className="px-4 pt-4 max-w-lg mx-auto space-y-4">
        <div className="flex gap-2">
          <Button
            variant={activeTab === 'ledgers' ? 'primary' : 'secondary'}
            className="flex-1"
            onClick={() => setActiveTab('ledgers')}
          >
            Ledgers
          </Button>
          <Button
            variant={activeTab === 'items' ? 'primary' : 'secondary'}
            className="flex-1"
            onClick={() => setActiveTab('items')}
          >
            Items
          </Button>
        </div>

        <Card className="flex flex-col gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder={`Search ${activeTab}...`}
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 focus:bg-white dark:focus:bg-gray-900 focus:ring-2 focus:ring-teal-500/50 outline-none text-gray-900 dark:text-gray-100"
            />
          </div>

          <div className="flex flex-wrap gap-2">
            {[
              { id: 'all', label: 'All' },
              { id: 'in_tally', label: 'In Tally' },
              { id: 'in_queue', label: 'In Queue' },
              { id: 'failed', label: 'Failed' }
            ].map(f => (
              <button
                key={f.id}
                onClick={() => setStatusFilter(f.id)}
                className={`px-3 py-1 text-xs font-bold rounded-full transition-colors ${statusFilter === f.id
                    ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-700'
                  }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </Card>

        {loading && data.ledgers.length === 0 ? (
          <div className="text-center py-12 text-sm font-medium text-gray-400">Loading masters...</div>
        ) : error ? (
          <div className="text-center py-12 text-sm font-medium text-red-500">{error}</div>
        ) : activeTab === 'ledgers' ? (
          <div className="space-y-6 pb-6">
            {sortedGroups.length === 0 && <div className="text-center py-8 text-gray-400 text-sm">No ledgers found</div>}
            {sortedGroups.map(group => (
              <div key={group} className="space-y-2">
                <div className="flex items-center justify-between px-1">
                  <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">{group}</h3>
                  <span className="text-xs font-bold text-gray-400 bg-gray-200 dark:bg-gray-800 px-2 py-0.5 rounded-full">
                    {groupedLedgers[group].length}
                  </span>
                </div>
                <Card className="divide-y divide-gray-100 dark:divide-gray-800 !p-0">
                  {groupedLedgers[group].map((ledger, idx) => (
                    <div key={idx} className="flex items-center justify-between p-3 sm:p-4 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors">
                      <div className="flex flex-col gap-0.5">
                        <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">{ledger.name}</span>
                        {ledger.cost_centre && (
                          <span className="text-[10px] text-gray-500 font-medium">Cost Centre Enabled</span>
                        )}
                        {ledger.status === 'failed' && ledger.error && (
                          <span className="text-xs text-red-500 mt-1">{ledger.error}</span>
                        )}
                      </div>
                      <StatusBadge status={ledger.status} />
                    </div>
                  ))}
                </Card>
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-3 pb-6">
            {sortedItems.length === 0 && <div className="text-center py-8 text-gray-400 text-sm">No items found</div>}
            <Card className="divide-y divide-gray-100 dark:divide-gray-800 !p-0">
              {sortedItems.map((item, idx) => (
                <div key={idx} className="flex items-center justify-between p-3 sm:p-4 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors">
                  <div className="flex flex-col gap-0.5">
                    <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">{item.name}</span>
                    <span className="text-[10px] text-gray-500 font-bold tracking-wider">{item.unit}</span>
                    {item.status === 'failed' && item.error && (
                      <span className="text-xs text-red-500 mt-1">{item.error}</span>
                    )}
                  </div>
                  <StatusBadge status={item.status} />
                </div>
              ))}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
