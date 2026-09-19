'use client';
import { useState, useEffect } from 'react';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { useUI } from '@/context/UIContext';
import { AlertCircle, CheckCircle2, Search, RefreshCw, XCircle, Pencil, Trash2, ArrowRight } from 'lucide-react';

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
  const { showToast, showConfirmDialog } = useUI();
  const [activeTab, setActiveTab] = useState('ledgers');
  const [data, setData] = useState({ ledgers: [], items: [], counts: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [aliases, setAliases] = useState([]);
  const [aliasesLoading, setAliasesLoading] = useState(true);
  const [editingAliasId, setEditingAliasId] = useState(null);

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

  const fetchAliases = async () => {
    setAliasesLoading(true);
    try {
      const res = await fetch("/api/masters/aliases");
      if (!res.ok) throw new Error("Failed to fetch aliases");
      const json = await res.json();
      setAliases(json.aliases || []);
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setAliasesLoading(false);
    }
  };

  useEffect(() => {
    fetchMasters();
    fetchAliases();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSaveAlias = async (aliasId, newMappedName) => {
    if (!newMappedName || !newMappedName.trim()) return;
    try {
      const res = await fetch(`/api/masters/aliases/${aliasId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mapped_name: newMappedName.trim() })
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to update alias");
      }
      setAliases(prev => prev.map(a => a.id === aliasId ? { ...a, mapped_name: newMappedName.trim() } : a));
      setEditingAliasId(null);
      showToast("Mapping updated");
    } catch (err) {
      showToast(err.message, 'error');
    }
  };

  const handleDeleteAlias = (aliasId, originalName) => {
    showConfirmDialog({
      title: "Remove this mapping?",
      message: `"${originalName || '(empty)'}" will go through normal matching fresh next time, instead of resolving automatically.`,
      danger: true,
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/masters/aliases/${aliasId}`, { method: 'DELETE' });
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || "Failed to remove mapping");
          }
          setAliases(prev => prev.filter(a => a.id !== aliasId));
          showToast("Mapping removed");
        } catch (err) {
          showToast(err.message, 'error');
        }
      }
    });
  };

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

  // Filter + sort aliases (search matches either the raw extracted name or
  // the Tally item it currently resolves to)
  const filteredAliases = aliases.filter(a => {
    const s = normalizeStr(search);
    return s === "" || normalizeStr(a.original_name).includes(s) || normalizeStr(a.mapped_name).includes(s);
  });
  const sortedAliases = [...filteredAliases].sort((a, b) => (a.original_name || '').localeCompare(b.original_name || ''));

  const stockItemNames = Array.from(new Set(data.items.map(i => i.name))).sort();

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
          <Button
            variant={activeTab === 'aliases' ? 'primary' : 'secondary'}
            className="flex-1"
            onClick={() => setActiveTab('aliases')}
          >
            Mappings
          </Button>
        </div>

        <Card className="flex flex-col gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder={activeTab === 'aliases' ? 'Search mappings...' : `Search ${activeTab}...`}
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 focus:bg-white dark:focus:bg-gray-900 focus:ring-2 focus:ring-teal-500/50 outline-none text-gray-900 dark:text-gray-100"
            />
          </div>

          {activeTab !== 'aliases' && (
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
          )}
        </Card>

        {activeTab === 'aliases' && (
          <p className="text-xs text-gray-500 dark:text-gray-400 px-1 -mt-2">
            When an extracted item name is matched to a Tally item, it&apos;s remembered here so future invoices with that
            same wording map automatically. If one was mapped to the wrong item, fix or remove it below.
          </p>
        )}

        {(activeTab === 'aliases' ? aliasesLoading && aliases.length === 0 : loading && data.ledgers.length === 0) ? (
          <div className="text-center py-12 text-sm font-medium text-gray-400">Loading...</div>
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
        ) : activeTab === 'items' ? (
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
        ) : (
          <div className="space-y-3 pb-6">
            {sortedAliases.length === 0 && <div className="text-center py-8 text-gray-400 text-sm">No mappings yet</div>}
            <Card className="divide-y divide-gray-100 dark:divide-gray-800 !p-0">
              {sortedAliases.map((alias) => (
                <div key={alias.id} className="flex flex-col gap-2 p-3 sm:p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex flex-col gap-1 min-w-0 flex-1">
                      <span className="text-xs text-gray-500 dark:text-gray-400 break-words">
                        {alias.original_name || <span className="italic">(empty)</span>}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <ArrowRight className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                        {editingAliasId === alias.id ? (
                          <SearchableSelect
                            options={stockItemNames}
                            value={alias.mapped_name}
                            onChange={(val) => handleSaveAlias(alias.id, val)}
                            placeholder="Select correct item..."
                            className="flex-1"
                          />
                        ) : (
                          <span className="text-sm font-semibold text-gray-900 dark:text-gray-100 break-words">{alias.mapped_name}</span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      {editingAliasId === alias.id ? (
                        <Button variant="secondary" className="!min-h-0 !w-auto px-3 py-1.5 text-xs" onClick={() => setEditingAliasId(null)}>
                          Cancel
                        </Button>
                      ) : (
                        <button
                          onClick={() => setEditingAliasId(alias.id)}
                          className="p-2 text-gray-400 hover:text-teal-600 dark:hover:text-teal-400 transition-colors"
                          aria-label="Edit mapping"
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        onClick={() => handleDeleteAlias(alias.id, alias.original_name)}
                        className="p-2 text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition-colors"
                        aria-label="Remove mapping"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
