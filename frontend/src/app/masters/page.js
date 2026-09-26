'use client';
import { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import TopBar from '@/components/layout/TopBar';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { useUI } from '@/context/UIContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import {
  RefreshCw, Search, Pencil, Trash2, ArrowRight, X, ChevronRight, ChevronDown, ArrowUp, ArrowDown, Plus
} from 'lucide-react';

const TABS = [
  { key: 'ledgers', label: 'Ledgers' },
  { key: 'items', label: 'Items' },
  { key: 'cost_centres', label: 'Cost centres' },
  { key: 'mappings', label: 'Mappings' },
];

const PERIOD_PRESETS = ['month', 'last_month', 'quarter', 'fy', 'custom'];

function pad2(n) { return String(n).padStart(2, '0'); }
function fmtYYYYMMDD(d) { return `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}`; }

function getPresetRange(preset) {
  const today = new Date();
  const y = today.getFullYear();
  const m = today.getMonth();
  let start, end;
  if (preset === 'month') {
    start = new Date(y, m, 1);
    end = today;
  } else if (preset === 'last_month') {
    start = new Date(y, m - 1, 1);
    end = new Date(y, m, 0);
  } else if (preset === 'quarter') {
    const qMonth = Math.floor(m / 3) * 3;
    start = new Date(y, qMonth, 1);
    end = today;
  } else if (preset === 'fy') {
    const fyStartYear = m >= 3 ? y : y - 1;
    start = new Date(fyStartYear, 3, 1);
    end = today;
  } else {
    start = new Date(y, m, 1);
    end = today;
  }
  return { start: fmtYYYYMMDD(start), end: fmtYYYYMMDD(end) };
}

function presetLabel(preset) {
  if (preset === 'month') return 'This month';
  if (preset === 'last_month') {
    const d = new Date();
    d.setMonth(d.getMonth() - 1);
    return d.toLocaleDateString('en-US', { month: 'long' });
  }
  if (preset === 'quarter') return 'This quarter';
  if (preset === 'fy') return 'This FY';
  return 'Custom';
}

function formatDateInput(yyyymmdd) {
  if (!yyyymmdd || yyyymmdd.length !== 8) return '';
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}
function parseDateInput(v) { return (v || '').replace(/-/g, ''); }

function formatDateShort(yyyymmdd) {
  if (!yyyymmdd || yyyymmdd.length !== 8) return yyyymmdd || '';
  const d = new Date(+yyyymmdd.slice(0, 4), +yyyymmdd.slice(4, 6) - 1, +yyyymmdd.slice(6, 8));
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' }).format(d);
}
function formatDateFull(yyyymmdd) {
  if (!yyyymmdd || yyyymmdd.length !== 8) return yyyymmdd || '';
  const d = new Date(+yyyymmdd.slice(0, 4), +yyyymmdd.slice(4, 6) - 1, +yyyymmdd.slice(6, 8));
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' }).format(d).toUpperCase();
}

function formatItemDate(d) {
  if (!d) return '';
  const parsed = new Date(d.includes('T') ? d : d + 'T00:00:00');
  if (isNaN(parsed.getTime())) return d;
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(parsed);
}

function formatMoney(v) {
  const n = Math.abs(v || 0);
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatLastFetched(iso) {
  if (!iso) return null;
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  if (isNaN(d.getTime())) return null;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return sameDay ? `today at ${time}` : `${d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })} at ${time}`;
}

function StatusTag({ children }) {
  return <span className="text-accent-700">{children}</span>;
}

function PillChip({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`h-9 px-3.5 rounded-full border text-[12.5px] whitespace-nowrap shrink-0 transition-colors ${active ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider text-text hover:bg-text/5'
        }`}
    >
      {children}
    </button>
  );
}

// --- Ledger detail sheet -----------------------------------------------

function LedgerSheet({ ledger, onClose }) {
  const [preset, setPreset] = useState('month');
  const [range, setRange] = useState(getPresetRange('month'));
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (preset !== 'custom') setRange(getPresetRange(preset));
  }, [preset]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/reporting/creditors/${encodeURIComponent(ledger.name)}?start_date=${range.start}&end_date=${range.end}`)
      .then(async res => {
        const json = await res.json();
        if (!res.ok) throw new Error(json.detail || 'Failed to load ledger.');
        return json;
      })
      .then(json => { if (!cancelled) setData(json); })
      .catch(err => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ledger.name, range.start, range.end]);

  // Dr/Cr suffix only means something unambiguous for debtor/creditor
  // ledgers -- for everything else (banks, expenses, etc.) just show a
  // plain signed figure rather than guess at a convention.
  const isDebtorCreditor = ledger.parent === 'Sundry Debtors' || ledger.parent === 'Sundry Creditors';
  const suffix = (v) => isDebtorCreditor ? ` ${v >= 0 ? 'Dr' : 'Cr'}` : '';

  const movements = data?.movements || [];
  const debits = movements.filter(m => (m.amount || 0) > 0).reduce((a, m) => a + m.amount, 0);
  const credits = movements.filter(m => (m.amount || 0) < 0).reduce((a, m) => a + Math.abs(m.amount), 0);

  let running = data?.period_opening ?? 0;
  const rows = movements.map(m => {
    running += (m.amount || 0);
    return { ...m, runningBalance: running };
  });

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-x-0 bottom-0 top-12 z-50 bg-bg rounded-t-lg shadow-lg flex flex-col">
        <div className="flex items-start justify-between px-5 pt-5 pb-4 border-b border-divider shrink-0">
          <div className="min-w-0">
            <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700 truncate">
              Ledger &middot; {ledger.parent}
            </div>
            <h2 className="font-heading font-semibold text-2xl truncate">{ledger.name}</h2>
          </div>
          <button onClick={onClose} className="p-1 -mr-1 -mt-1 text-neutral-600 hover:text-text shrink-0" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-5 pb-8">
          <div className="flex gap-2 overflow-x-auto py-4 -mx-1 px-1" style={{ scrollbarWidth: 'none' }}>
            {PERIOD_PRESETS.map(p => (
              <PillChip key={p} active={preset === p} onClick={() => setPreset(p)}>{presetLabel(p)}</PillChip>
            ))}
          </div>

          {preset === 'custom' && (
            <div className="grid grid-cols-2 gap-2.5 pb-4">
              <Input label="From" type="date" value={formatDateInput(range.start)} onChange={e => setRange(r => ({ ...r, start: parseDateInput(e.target.value) }))} />
              <Input label="To" type="date" value={formatDateInput(range.end)} onChange={e => setRange(r => ({ ...r, end: parseDateInput(e.target.value) }))} />
            </div>
          )}

          {loading ? (
            <p className="text-sm text-neutral-600 py-8 text-center">Loading&hellip;</p>
          ) : error ? (
            <p className="text-sm text-accent-700 py-8 text-center">{error}</p>
          ) : (
            <>
              <div className="grid grid-cols-2 border-t border-divider">
                <div className="py-3.5 pr-3 border-b border-divider">
                  <div className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Opening</div>
                  <div className="font-heading text-xl mt-1">{formatMoney(data.period_opening)}{suffix(data.period_opening)}</div>
                </div>
                <div className="py-3.5 pl-3 border-b border-l border-divider">
                  <div className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Debits</div>
                  <div className="font-heading text-xl mt-1">{formatMoney(debits)}</div>
                </div>
                <div className="py-3.5 pr-3">
                  <div className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Credits</div>
                  <div className="font-heading text-xl mt-1">{formatMoney(credits)}</div>
                </div>
                <div className="py-3.5 pl-3 border-l border-divider">
                  <div className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Closing</div>
                  <div className="font-heading text-xl mt-1 text-accent-700">{formatMoney(data.period_closing)}{suffix(data.period_closing)}</div>
                </div>
              </div>

              <div className="flex justify-between items-baseline mt-5 mb-1 text-[11px] text-neutral-700">
                <span>{formatDateFull(range.start)} &ndash; {formatDateFull(range.end)} &middot; {rows.length} entries</span>
                <span className="tracking-[0.1em] uppercase">Balance</span>
              </div>

              <div className="border-t border-divider">
                <div className="flex justify-between py-3 border-b border-divider font-semibold">
                  <span>Opening balance</span>
                  <span>{formatMoney(data.period_opening)}{suffix(data.period_opening)}</span>
                </div>
                {rows.map((m, i) => (
                  <div key={i} className="flex justify-between items-start gap-3 py-3 border-b border-divider">
                    <div className="min-w-0">
                      <div className="text-[15px]">
                        {m.voucher_type}
                        {m.is_pending && <span className="ml-2 text-xs text-accent-700">Waiting for Tally</span>}
                      </div>
                      <div className="text-sm text-neutral-700 truncate">{m.voucher_number || m.reference || ''}</div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className={m.amount < 0 ? 'text-accent-700' : 'text-text'}>
                        {m.amount >= 0 ? '+ ' : '− '}{formatMoney(m.amount)}
                      </div>
                      <div className="text-sm text-neutral-700 mt-0.5">{formatMoney(m.runningBalance)}{suffix(m.runningBalance)}</div>
                    </div>
                  </div>
                ))}
                <div className="flex justify-between py-3 font-semibold">
                  <span>Closing balance</span>
                  <span>{formatMoney(data.period_closing)}{suffix(data.period_closing)}</span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

// --- Item detail sheet --------------------------------------------------

function ItemSheet({ itemName, onClose }) {
  const [state, setState] = useState({ loading: true, error: null, source: null, entries: [] });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, source: null, entries: [] });
    fetch('/api/purchase-item/return-item-history?' + new URLSearchParams({ item_name: itemName }))
      .then(async res => {
        const json = await res.json();
        if (!res.ok) throw new Error(json.detail || 'Failed to fetch purchase history.');
        return json;
      })
      .then(json => { if (!cancelled) setState({ loading: false, error: null, source: json.source, entries: json.entries || [] }); })
      .catch(err => { if (!cancelled) setState({ loading: false, error: err.message, source: null, entries: [] }); });
    return () => { cancelled = true; };
  }, [itemName]);

  const { entries } = state;
  const rates = entries.map(e => e.rate).filter(r => r != null);
  const last = entries[0];
  const lowestEntry = entries.reduce((a, e) => (a == null || e.rate < a.rate ? e : a), null);
  const highestEntry = entries.reduce((a, e) => (a == null || e.rate > a.rate ? e : a), null);

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-x-0 bottom-0 top-12 z-50 bg-bg rounded-t-lg shadow-lg flex flex-col">
        <div className="flex items-start justify-between px-5 pt-5 pb-4 border-b border-divider shrink-0">
          <h2 className="font-heading font-semibold text-2xl truncate">{itemName}</h2>
          <button onClick={onClose} className="p-1 -mr-1 -mt-1 text-neutral-600 hover:text-text shrink-0" aria-label="Close">
            <ChevronDown className="w-5 h-5" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-5 pb-8">
          {state.loading ? (
            <p className="text-sm text-neutral-600 py-8 text-center">Loading&hellip;</p>
          ) : state.error ? (
            <p className="text-sm text-accent-700 py-8 text-center">{state.error}</p>
          ) : (
            <>
              {state.source === 'local_cache' && (
                <div className="mt-4 border border-accent rounded-md px-4 py-3 text-sm text-accent-700">
                  Showing last known rates &mdash; Tally is offline right now.
                </div>
              )}

              {entries.length === 0 ? (
                <p className="text-sm text-neutral-600 py-8 text-center">No purchase history in the last 2 years for this item.</p>
              ) : (
                <>
                  <div className="grid grid-cols-3 border-t border-b border-divider mt-4">
                    <div className="py-3.5 pr-3">
                      <div className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Last rate</div>
                      <div className="font-heading text-xl mt-1">{formatMoney(last?.rate)}</div>
                      <div className="text-xs text-neutral-600 mt-0.5 truncate">{last?.supplier || '—'}</div>
                    </div>
                    <div className="py-3.5 px-3 border-l border-divider">
                      <div className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Lowest</div>
                      <div className="font-heading text-xl mt-1">{formatMoney(lowestEntry?.rate)}</div>
                      <div className="text-xs text-neutral-600 mt-0.5 truncate">{lowestEntry?.supplier || '—'}</div>
                    </div>
                    <div className="py-3.5 pl-3 border-l border-divider">
                      <div className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Highest</div>
                      <div className="font-heading text-xl mt-1">{formatMoney(highestEntry?.rate)}</div>
                      <div className="text-xs text-neutral-600 mt-0.5 truncate">{highestEntry?.supplier || '—'}</div>
                    </div>
                  </div>

                  <div className="mt-2">
                    {entries.map((e, i) => {
                      const prev = entries[i + 1];
                      const diff = prev ? +(e.rate - prev.rate).toFixed(2) : 0;
                      return (
                        <div key={i} className="py-3.5 border-b border-divider">
                          <div className="flex justify-between items-start gap-3">
                            <div className="min-w-0">
                              <div className="text-[15px]">{formatItemDate(e.date)}</div>
                              <div className="text-sm text-neutral-700 truncate">
                                {e.origin === 'repack' ? 'Made in-house (Repack)' : (e.supplier || 'Unknown supplier')}
                              </div>
                              {e.origin === 'app_post' && <div className="mt-1"><StatusTag>Pending Tally sync</StatusTag></div>}
                            </div>
                            <div className="text-right shrink-0">
                              <div className="font-heading font-semibold text-lg">{formatMoney(e.rate)}</div>
                              <div className="text-sm text-neutral-700 mt-0.5 whitespace-nowrap">
                                {e.voucher_number || 'No voucher #'} &middot; Qty {e.qty ?? '—'} {e.unit || ''}
                              </div>
                              {diff !== 0 && (
                                <div className="text-xs text-accent-700 flex items-center justify-end gap-1 mt-0.5">
                                  {diff > 0 ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />}
                                  {formatMoney(diff)} vs previous
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

function CreateLedgerSheet({ groups, costCentreDefaults, form, onChange, onParentChange, onSubmit, submitting, onClose }) {
  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-x-0 bottom-0 sm:inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 pointer-events-none">
        <div
          className="bg-surface border-t sm:border border-divider rounded-t-2xl sm:rounded-lg shadow-lg w-full sm:max-w-md flex flex-col pointer-events-auto animate-in slide-in-from-bottom-full sm:zoom-in-95"
          onClick={e => e.stopPropagation()}
        >
          <div className="w-12 h-1.5 bg-divider rounded-full mx-auto mt-3 sm:hidden" />

          <div className="flex items-center justify-between px-6 py-4 border-b border-divider">
            <h3 className="font-heading font-semibold text-xl">Create ledger</h3>
            <button onClick={onClose} className="text-neutral-500 hover:text-text transition-colors" aria-label="Close">
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="px-6 py-4 flex flex-col gap-3">
            <Input
              label="Ledger name"
              value={form.name}
              onChange={e => onChange({ ...form, name: e.target.value })}
              placeholder="e.g. Rent Expense"
            />
            <Select label="Group" value={form.parent} onChange={e => onParentChange(e.target.value)}>
              <option value="" disabled>Select a group&hellip;</option>
              {groups.map(g => <option key={g} value={g}>{g}</option>)}
            </Select>

            <label className="flex items-start gap-3 p-3 rounded-md border border-divider cursor-pointer">
              <input
                type="checkbox"
                checked={form.cost_centre}
                onChange={e => onChange({ ...form, cost_centre: e.target.checked })}
                className="w-4 h-4 mt-0.5 accent-[var(--color-accent)] rounded"
              />
              <span>
                <span className="block text-[15px] font-medium">Track by cost centre (store)</span>
                {form.parent && costCentreDefaults[form.parent] && (
                  <span className="block text-sm text-neutral-700 mt-0.5">
                    Other ledgers under &ldquo;{form.parent}&rdquo; already track a cost centre.
                  </span>
                )}
              </span>
            </label>
          </div>

          <div className="px-6 py-4 border-t border-divider">
            <Button onClick={onSubmit} disabled={submitting} className="w-full">
              {submitting ? 'Creating…' : 'Create ledger'}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

// --- Main page ------------------------------------------------------------

export default function MastersPage() {
  const router = useRouter();
  const { showToast, showConfirmDialog } = useUI();
  const { lastSyncedAt } = useSyncStatus();
  const [activeTab, setActiveTab] = useState('ledgers');
  const [data, setData] = useState({ ledgers: [], items: [], cost_centres: [], counts: null, company_name: null, financial_year: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [aliases, setAliases] = useState([]);
  const [aliasesLoading, setAliasesLoading] = useState(true);
  const [editingAliasId, setEditingAliasId] = useState(null);

  const [search, setSearch] = useState('');
  const [parentFilter, setParentFilter] = useState('All');

  const [openLedger, setOpenLedger] = useState(null);
  const [openItemName, setOpenItemName] = useState(null);

  const [creatingLedgerOpen, setCreatingLedgerOpen] = useState(false);
  const [newLedger, setNewLedger] = useState({ name: '', parent: '', cost_centre: false });
  const [creatingLedger, setCreatingLedger] = useState(false);

  const fetchMasters = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/masters');
      if (!res.ok) throw new Error('Failed to fetch masters');
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
      const res = await fetch('/api/masters/aliases');
      if (!res.ok) throw new Error('Failed to fetch aliases');
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

  useEffect(() => { setParentFilter('All'); setSearch(''); }, [activeTab]);

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
        throw new Error(body.detail || 'Failed to update alias');
      }
      setAliases(prev => prev.map(a => a.id === aliasId ? { ...a, mapped_name: newMappedName.trim() } : a));
      setEditingAliasId(null);
      showToast('Mapping updated');
    } catch (err) {
      showToast(err.message, 'error');
    }
  };

  const handleDeleteAlias = (aliasId, originalName) => {
    showConfirmDialog({
      title: 'Remove this mapping?',
      message: `"${originalName || '(empty)'}" will go through normal matching fresh next time, instead of resolving automatically.`,
      danger: true,
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/masters/aliases/${aliasId}`, { method: 'DELETE' });
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || 'Failed to remove mapping');
          }
          setAliases(prev => prev.filter(a => a.id !== aliasId));
          showToast('Mapping removed');
        } catch (err) {
          showToast(err.message, 'error');
        }
      }
    });
  };

  const normalizeStr = (str) => (str || '').replace(/\s+/g, ' ').trim().toLowerCase();

  const filteredLedgers = useMemo(() => data.ledgers.filter(l => {
    const s = normalizeStr(search);
    const matchesSearch = s === '' || normalizeStr(l.name).includes(s) || normalizeStr(l.parent).includes(s);
    const matchesParent = parentFilter === 'All' || l.parent === parentFilter;
    return matchesSearch && matchesParent;
  }).sort((a, b) => a.name.localeCompare(b.name)), [data.ledgers, search, parentFilter]);

  const ledgerParents = useMemo(() => {
    const counts = {};
    data.ledgers.forEach(l => { counts[l.parent] = (counts[l.parent] || 0) + 1; });
    return Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  }, [data.ledgers]);

  // Tally has no group-level "requires a cost centre" rule -- it's a flag on
  // each individual ledger. This just infers a sensible default from how the
  // group is already used: if any existing ledger under it already tracks a
  // cost centre, a new one probably should too. Still just a starting point
  // -- the checkbox below stays editable.
  const parentCostCentreMap = useMemo(() => {
    const map = {};
    data.ledgers.forEach(l => { if (l.parent && l.cost_centre) map[l.parent] = true; });
    return map;
  }, [data.ledgers]);

  const handleParentChange = (parent) => {
    setNewLedger(prev => ({ ...prev, parent, cost_centre: !!parentCostCentreMap[parent] }));
  };

  const handleCreateLedger = async () => {
    const name = newLedger.name.trim();
    if (!name) { showToast('Enter a ledger name', 'error'); return; }
    if (!newLedger.parent) { showToast('Select a group', 'error'); return; }

    setCreatingLedger(true);
    try {
      const res = await fetch('/api/masters/create-ledger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, parent: newLedger.parent, cost_center: newLedger.cost_centre }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Failed to create ledger');
      showToast(body.message || 'Ledger created');
      setCreatingLedgerOpen(false);
      setNewLedger({ name: '', parent: '', cost_centre: false });
      fetchMasters();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setCreatingLedger(false);
    }
  };

  const filteredItems = useMemo(() => data.items.filter(i => {
    const s = normalizeStr(search);
    return s === '' || normalizeStr(i.name).includes(s) || normalizeStr(i.unit).includes(s);
  }).sort((a, b) => a.name.localeCompare(b.name)), [data.items, search]);

  const filteredCostCentres = useMemo(() => data.cost_centres.filter(c => {
    const s = normalizeStr(search);
    return s === '' || normalizeStr(c.name).includes(s);
  }).sort((a, b) => a.name.localeCompare(b.name)), [data.cost_centres, search]);

  const filteredAliases = aliases.filter(a => {
    const s = normalizeStr(search);
    return s === '' || normalizeStr(a.original_name).includes(s) || normalizeStr(a.mapped_name).includes(s);
  }).sort((a, b) => (a.original_name || '').localeCompare(b.original_name || ''));

  const stockItemNames = Array.from(new Set(data.items.map(i => i.name))).sort();

  const waitingTotal = data.counts
    ? (data.counts.ledgers.in_queue + data.counts.ledgers.syncing + data.counts.items.in_queue + data.counts.items.syncing)
    : 0;

  const lastFetched = formatLastFetched(lastSyncedAt);

  return (
    <div className="min-h-screen bg-bg pb-20">
      <TopBar title="Masters overview" kicker="From Tally" showBack onBack={() => router.push('/dashboard')} />

      <div className="max-w-md mx-auto px-5">

        <div className="flex items-start justify-between gap-3 py-5 border-b border-divider">
          <div className="min-w-0">
            <div className="text-[11px] tracking-[0.1em] uppercase text-accent-700 truncate">
              From Tally {data.company_name && `· ${data.company_name}${data.financial_year ? ` ${data.financial_year}` : ''}`}
            </div>
            {lastFetched && <div className="text-sm text-neutral-700 mt-1">Last fetched {lastFetched}</div>}
          </div>
          <button
            onClick={fetchMasters}
            className="h-9 px-3.5 flex items-center gap-2 border border-divider rounded-md text-sm shrink-0 hover:border-accent transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
        </div>

        {data.counts && (
          <div className="grid grid-cols-4 border-b border-divider">
            {[
              ['Ledgers', data.counts.ledgers.total],
              ['Items', data.counts.items.total],
              ['Cost centres', data.counts.cost_centres.total],
              ['Waiting', waitingTotal],
            ].map(([label, count], i) => (
              <div key={label} className={`py-4 ${i > 0 ? 'border-l border-divider pl-3' : 'pr-3'}`}>
                <div className="font-heading text-2xl leading-tight">{count}</div>
                <div className="text-sm text-neutral-700 mt-0.5">{label}</div>
              </div>
            ))}
          </div>
        )}

        <div className="grid grid-cols-4 border border-divider rounded-md overflow-hidden my-4">
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`h-11 text-[13px] text-center px-1 transition-colors ${activeTab === t.key ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'
                }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {activeTab !== 'mappings' && (
          <div className="relative mb-3">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-neutral-500" />
            <input
              type="text"
              placeholder={`Search ${activeTab === 'cost_centres' ? 'cost centres' : activeTab}`}
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full h-12 pl-10 pr-4 rounded-md border border-divider bg-transparent text-text placeholder-neutral-500 focus:outline-none focus-visible:border-accent hover:border-text/45 transition-colors"
            />
          </div>
        )}

        {activeTab === 'mappings' && (
          <p className="text-xs text-neutral-600 mb-3">
            When an extracted item name is matched to a Tally item, it&apos;s remembered here so future invoices with that
            same wording map automatically. If one was mapped to the wrong item, fix or remove it below.
          </p>
        )}

        {activeTab === 'ledgers' && (
          <>
            <div className="flex gap-2 overflow-x-auto pb-3 -mx-1 px-1" style={{ scrollbarWidth: 'none' }}>
              <PillChip active={parentFilter === 'All'} onClick={() => setParentFilter('All')}>All</PillChip>
              {ledgerParents.map(p => (
                <PillChip key={p} active={parentFilter === p} onClick={() => setParentFilter(p)}>{p}</PillChip>
              ))}
            </div>

            <button
              onClick={() => setCreatingLedgerOpen(true)}
              className="w-full flex items-center justify-center gap-2 h-11 border border-dashed border-accent rounded-md text-accent-700 mb-3 hover:bg-accent/5 transition-colors"
            >
              <Plus className="w-4 h-4" /> Create ledger
            </button>
          </>
        )}

        {(activeTab === 'mappings' ? aliasesLoading && aliases.length === 0 : loading && data.ledgers.length === 0) ? (
          <div className="text-center py-12 text-sm text-neutral-600">Loading&hellip;</div>
        ) : error ? (
          <div className="text-center py-12 text-sm text-accent-700">{error}</div>
        ) : activeTab === 'ledgers' ? (
          <div>
            {filteredLedgers.length === 0 && <div className="text-center py-8 text-neutral-600 text-sm">No ledgers found</div>}
            {filteredLedgers.map((ledger, idx) => (
              <button
                key={idx}
                onClick={() => setOpenLedger(ledger)}
                className="w-full flex items-center justify-between gap-3 py-3.5 border-b border-divider text-left hover:bg-text/4 transition-colors"
              >
                <div className="min-w-0">
                  <div className="text-[15px] truncate">{ledger.name}</div>
                  <div className="text-sm text-neutral-700 truncate">
                    {ledger.parent}{ledger.status !== 'in_tally' && <> &middot; <StatusTag>Waiting for Tally</StatusTag></>}
                  </div>
                  {ledger.status === 'failed' && ledger.error && (
                    <div className="text-xs text-accent-800 mt-0.5">{ledger.error}</div>
                  )}
                </div>
                <ChevronRight className="w-4 h-4 text-neutral-500 shrink-0" />
              </button>
            ))}
          </div>
        ) : activeTab === 'items' ? (
          <div>
            {filteredItems.length === 0 && <div className="text-center py-8 text-neutral-600 text-sm">No items found</div>}
            {filteredItems.map((item, idx) => (
              <button
                key={idx}
                onClick={() => setOpenItemName(item.name)}
                className="w-full flex items-center justify-between gap-3 py-3.5 border-b border-divider text-left hover:bg-text/4 transition-colors"
              >
                <div className="min-w-0">
                  <div className="text-[15px] truncate">{item.name}</div>
                  <div className="text-sm text-neutral-700 truncate">
                    {item.unit}{item.status !== 'in_tally' && <> &middot; <StatusTag>Waiting for Tally</StatusTag></>}
                  </div>
                  {item.status === 'failed' && item.error && (
                    <div className="text-xs text-accent-800 mt-0.5">{item.error}</div>
                  )}
                </div>
                <ChevronRight className="w-4 h-4 text-neutral-500 shrink-0" />
              </button>
            ))}
          </div>
        ) : activeTab === 'cost_centres' ? (
          <div>
            {filteredCostCentres.length === 0 && <div className="text-center py-8 text-neutral-600 text-sm">No cost centres found</div>}
            {filteredCostCentres.map((cc, idx) => (
              <div key={idx} className="py-3.5 border-b border-divider">
                <div className="text-[15px]">{cc.name}</div>
                {cc.parent && <div className="text-sm text-neutral-700 mt-0.5">{cc.parent}</div>}
              </div>
            ))}
          </div>
        ) : (
          <div>
            {filteredAliases.length === 0 && <div className="text-center py-8 text-neutral-600 text-sm">No mappings yet</div>}
            {filteredAliases.map(alias => (
              <div key={alias.id} className="flex flex-col gap-2 py-3.5 border-b border-divider">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex flex-col gap-1 min-w-0 flex-1">
                    <span className="text-xs text-neutral-600 break-words">
                      {alias.original_name || <span className="italic">(empty)</span>}
                    </span>
                    <div className="flex items-center gap-1.5">
                      <ArrowRight className="w-3.5 h-3.5 text-neutral-500 shrink-0" />
                      {editingAliasId === alias.id ? (
                        <SearchableSelect
                          options={stockItemNames}
                          value={alias.mapped_name}
                          onChange={(val) => handleSaveAlias(alias.id, val)}
                          placeholder="Select correct item..."
                          className="flex-1"
                        />
                      ) : (
                        <span className="text-[15px] break-words">{alias.mapped_name}</span>
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
                        className="p-2 text-neutral-600 hover:text-accent-700 transition-colors"
                        aria-label="Edit mapping"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                    )}
                    <button
                      onClick={() => handleDeleteAlias(alias.id, alias.original_name)}
                      className="p-2 text-neutral-600 hover:text-red-600 transition-colors"
                      aria-label="Remove mapping"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {openLedger && <LedgerSheet ledger={openLedger} onClose={() => setOpenLedger(null)} />}
      {openItemName && <ItemSheet itemName={openItemName} onClose={() => setOpenItemName(null)} />}
      {creatingLedgerOpen && (
        <CreateLedgerSheet
          groups={ledgerParents}
          costCentreDefaults={parentCostCentreMap}
          form={newLedger}
          onChange={setNewLedger}
          onParentChange={handleParentChange}
          onSubmit={handleCreateLedger}
          submitting={creatingLedger}
          onClose={() => setCreatingLedgerOpen(false)}
        />
      )}
    </div>
  );
}
