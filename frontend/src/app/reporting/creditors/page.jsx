"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import TopBar from '@/components/layout/TopBar';
import ReportTabs from '@/components/layout/ReportTabs';
import { Wifi, WifiOff, RefreshCw, Search, ArrowUpDown, AlertTriangle, X, Package } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const PRESETS = [
  { label: "Today", value: "today" },
  { label: "7 days", value: "7days" },
  { label: "This month", value: "month" },
  { label: "30 days", value: "30days" },
  { label: "Custom", value: "custom" },
];

const SORT_OPTIONS = [
  { label: "Sort: Closing balance", value: "closing" },
  { label: "Sort: Supplier", value: "name" },
  { label: "Sort: Opening balance", value: "opening" },
  { label: "Sort: Purchases", value: "purchases" },
  { label: "Sort: Returns", value: "returns" },
  { label: "Sort: Payments", value: "payments" },
  { label: "Sort: Adjustments", value: "adjustments" },
];

// Whole-rupee amount, no sign -- used for the "Bought / Paid / Returned"
// row subline, matching the other reports' compact figures.
function fmtMoney(v) {
  const n = Math.round(Math.abs(v || 0));
  return `₹${n.toLocaleString('en-IN')}`;
}
// Paise-precise, no sign -- used for the ledger figure grid (Purchases,
// Returns, Payments, Adjustments), which aren't balances so don't take Cr/Dr.
function fmtMoney2(v) {
  const n = Math.abs(Number(v || 0));
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
// Paise-precise balance with a Cr/Dr suffix -- Credit means the supplier is
// owed money (we owe them); Debit means we're in credit with them.
function fmtCr(v) {
  const n = Number(v || 0);
  return `${fmtMoney2(n)} ${n < 0 ? 'Dr' : 'Cr'}`;
}

function pad2(n) { return String(n).padStart(2, '0'); }
function fmtYYYYMMDD(d) { return `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}`; }

function getPresetDates(preset, custom) {
  const today = new Date();
  const fmt = fmtYYYYMMDD;

  if (preset === "today") return { start: fmt(today), end: fmt(today) };
  if (preset === "7days") {
    const start = new Date(today); start.setDate(today.getDate() - 6);
    return { start: fmt(start), end: fmt(today) };
  }
  if (preset === "month") {
    const start = new Date(today.getFullYear(), today.getMonth(), 1);
    const end = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    return { start: fmt(start), end: fmt(end) };
  }
  if (preset === "30days") {
    const start = new Date(today); start.setDate(today.getDate() - 29);
    return { start: fmt(start), end: fmt(today) };
  }
  return custom || { start: fmt(today), end: fmt(today) };
}

const formatDateInput = (yyyymmdd) => {
  if (!yyyymmdd || yyyymmdd.length !== 8) return "";
  return `${yyyymmdd.substring(0, 4)}-${yyyymmdd.substring(4, 6)}-${yyyymmdd.substring(6, 8)}`;
};
const parseDateInput = (yyyy_mm_dd) => (yyyy_mm_dd ? yyyy_mm_dd.replace(/-/g, '') : "");

function toDate(yyyymmdd) {
  return new Date(+yyyymmdd.slice(0, 4), +yyyymmdd.slice(4, 6) - 1, +yyyymmdd.slice(6, 8));
}
function formatDateShort(yyyymmdd) {
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' }).format(toDate(yyyymmdd));
}
function formatDateFull(yyyymmdd) {
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(toDate(yyyymmdd));
}
function formatSyncTime(iso) {
  if (!iso) return null;
  const d = new Date(iso.includes('T') || iso.includes('Z') ? iso : iso.replace(' ', 'T') + 'Z');
  if (isNaN(d.getTime())) return null;
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export default function CreditorsReport() {
  const router = useRouter();
  const { user } = useAuth();
  const { isOnline, lastSyncedAt, isSyncing, triggerManualSync } = useSyncStatus();
  const isOwner = !user || user.is_owner;

  const [preset, setPreset] = useState("7days");
  const [customDates, setCustomDates] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [queueCount, setQueueCount] = useState(null);

  const [searchTerm, setSearchTerm] = useState("");
  const [sortKey, setSortKey] = useState("closing");
  const [sortDesc, setSortDesc] = useState(true);

  const [selectedSupplier, setSelectedSupplier] = useState(null);
  const [ledger, setLedger] = useState(null);
  const [ledgerLoading, setLedgerLoading] = useState(false);

  const [voucherModal, setVoucherModal] = useState(null); // { id, type }
  const [voucherData, setVoucherData] = useState(null);
  const [voucherLoading, setVoucherLoading] = useState(false);

  const dates = useMemo(() => getPresetDates(preset, customDates), [preset, customDates]);

  useEffect(() => {
    fetch('/api/dashboard/stats')
      .then(res => res.json())
      .then(d => setQueueCount(d.queue_count ?? 0))
      .catch(() => {});
  }, []);

  const fetchData = () => {
    if (!isOwner || !dates.start || !dates.end) return;
    setLoading(true);
    fetch(`${API_BASE}/api/reporting/creditors?start_date=${dates.start}&end_date=${dates.end}`)
      .then(res => { if (!res.ok) throw new Error("Failed to fetch creditors data"); return res.json(); })
      .then(d => { setData(d); setError(null); })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(fetchData, [isOwner, dates.start, dates.end]);

  const handleRefresh = async () => {
    await triggerManualSync();
    fetchData();
  };

  const fetchLedger = (supplierName) => {
    setSelectedSupplier(supplierName);
    setLedgerLoading(true);
    fetch(`${API_BASE}/api/reporting/creditors/${encodeURIComponent(supplierName)}?start_date=${dates.start}&end_date=${dates.end}`)
      .then(res => { if (!res.ok) throw new Error("Failed to fetch ledger"); return res.json(); })
      .then(d => {
        let runningBal = d.period_opening;
        const movements = d.movements.map(m => {
          runningBal += m.amount;
          return { ...m, running_balance: runningBal };
        });
        setLedger({ ...d, movements });
      })
      .catch(() => setLedger(null))
      .finally(() => setLedgerLoading(false));
  };

  const closeLedger = () => { setSelectedSupplier(null); setLedger(null); };

  const openVoucher = (m) => {
    if (!m.voucher_id || m.is_pending) return;
    setVoucherModal({ id: m.voucher_id, type: m.voucher_type });
    setVoucherLoading(true);
    const isPurchase = m.voucher_type === 'Purchase';
    const url = isPurchase
      ? `${API_BASE}/api/reporting/purchases/bills/${m.voucher_id}`
      : `${API_BASE}/api/reporting/vouchers/${m.voucher_id}`;
    fetch(url)
      .then(res => { if (!res.ok) throw new Error("Failed to fetch voucher"); return res.json(); })
      .then(d => setVoucherData(d))
      .catch(() => setVoucherData(null))
      .finally(() => setVoucherLoading(false));
  };
  const closeVoucher = () => { setVoucherModal(null); setVoucherData(null); };

  const creditors = data?.creditors || [];
  const totalPayable = creditors.reduce((sum, c) => sum + c.period_closing, 0);
  const withBalance = creditors.filter(c => Math.abs(c.period_closing) > 0.005).length;

  const filtered = creditors.filter(c => c.supplier_name.toLowerCase().includes(searchTerm.toLowerCase()));
  const sortFieldMap = {
    closing: 'period_closing', name: 'supplier_name', opening: 'period_opening',
    purchases: 'purchases', returns: 'returns', payments: 'payments', adjustments: 'other_adjustments',
  };
  const sorted = [...filtered].sort((a, b) => {
    const field = sortFieldMap[sortKey];
    const av = a[field], bv = b[field];
    if (typeof av === 'string') return sortDesc ? bv.localeCompare(av) : av.localeCompare(bv);
    return sortDesc ? bv - av : av - bv;
  });

  const now = new Date();
  const monthKicker = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(now).toUpperCase();
  const periodLabelMap = { today: 'TODAY', '7days': 'LAST 7 DAYS', month: 'THIS MONTH', '30days': 'LAST 30 DAYS' };
  const periodLabel = periodLabelMap[preset] || `${formatDateShort(dates.start)} – ${formatDateShort(dates.end)}`.toUpperCase();
  const syncTime = formatSyncTime(lastSyncedAt);

  if (!isOwner) {
    return (
      <div className="min-h-screen bg-bg pb-24">
        <TopBar title="Reports" kicker={monthKicker} />
        <ReportTabs />
        <div className="max-w-md mx-auto px-4 pt-8">
          <p className="text-sm text-neutral-600 text-center">The creditors report is only available to store owners.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg pb-24">
      <TopBar
        title="Reports"
        kicker={monthKicker}
        rightContent={
          <div className={`flex items-center gap-1.5 h-9 px-3 rounded-full border shrink-0 ${isOnline === false ? 'border-accent text-accent-700' : 'border-divider text-neutral-700'}`}>
            {isOnline === false ? <WifiOff className="w-3.5 h-3.5" /> : <Wifi className="w-3.5 h-3.5" />}
            <span className="text-xs font-medium whitespace-nowrap">
              {isOnline === false ? 'Offline' : 'Online'}{queueCount > 0 ? ` · ${queueCount} saved` : ''}
            </span>
          </div>
        }
      />
      <ReportTabs />

      <div className="max-w-md mx-auto px-4">
        <div className="flex gap-2 overflow-x-auto py-4 -mx-1 px-1" style={{ scrollbarWidth: 'none' }}>
          {PRESETS.map(p => (
            <button
              key={p.value}
              onClick={() => setPreset(p.value)}
              className={`shrink-0 px-3.5 h-9 rounded-full border text-sm whitespace-nowrap transition-colors ${preset === p.value ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider text-text hover:border-text/45'}`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {preset === "custom" && (
          <div className="flex gap-3 pb-4">
            <div className="flex-1">
              <label className="text-xs text-neutral-600 mb-1 block">Start date</label>
              <input
                type="date"
                value={formatDateInput(customDates?.start || dates.start)}
                onChange={e => setCustomDates(prev => ({ start: parseDateInput(e.target.value), end: prev?.end || dates.end }))}
                className="w-full h-10 px-3 rounded-md border border-divider bg-bg text-text text-sm focus:outline-none focus:border-accent"
              />
            </div>
            <div className="flex-1">
              <label className="text-xs text-neutral-600 mb-1 block">End date</label>
              <input
                type="date"
                value={formatDateInput(customDates?.end || dates.end)}
                onChange={e => setCustomDates(prev => ({ start: prev?.start || dates.start, end: parseDateInput(e.target.value) }))}
                className="w-full h-10 px-3 rounded-md border border-divider bg-bg text-text text-sm focus:outline-none focus:border-accent"
              />
            </div>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
            <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
            <p className="text-accent-700 text-xs leading-snug">{error}</p>
          </div>
        )}

        {loading ? (
          <p className="text-sm text-neutral-600 text-center py-16">Loading&hellip;</p>
        ) : error ? null : (
          <>
            {data?.is_data_complete === false && (
              <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
                <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                <p className="text-accent-700 text-xs leading-snug">
                  Reporting data for this period may be incomplete &mdash; run a Tally sync to make sure everything is up to date.
                </p>
              </div>
            )}

            {/* Summary */}
            <div className="flex justify-between items-start gap-3 pb-4">
              <div className="min-w-0">
                <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">Payable to suppliers · {periodLabel}</div>
                <div className="flex items-baseline gap-1 font-heading mt-1">
                  <span className="text-2xl text-neutral-600">₹</span>
                  <span className="text-[40px] leading-none">{Math.abs(totalPayable).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span>
                  <span className="text-lg text-neutral-700 ml-1">{totalPayable < 0 ? 'Dr' : 'Cr'}</span>
                </div>
                <div className="text-sm text-neutral-700 mt-1.5">{creditors.length} supplier{creditors.length === 1 ? '' : 's'} · {withBalance} with a balance</div>
              </div>
              <button
                onClick={handleRefresh}
                disabled={isSyncing}
                className="shrink-0 flex flex-col items-center gap-0.5 px-2 py-1.5 rounded-md text-accent-700 hover:bg-accent/8 transition-colors disabled:opacity-60"
              >
                <RefreshCw className={`w-4 h-4 ${isSyncing ? 'animate-spin' : ''}`} />
                <span className="text-[10.5px] text-neutral-700 whitespace-nowrap">{syncTime ? `Tally · ${syncTime}` : 'Sync now'}</span>
              </button>
            </div>

            <div className="border-t border-divider mb-4" />

            {/* Search + sort */}
            <div className="flex flex-wrap gap-2 mb-2">
              <div className="flex-1 min-w-[160px] flex items-center gap-2 h-[42px] px-3 rounded-md border border-divider text-neutral-600">
                <Search className="w-4 h-4 shrink-0" />
                <input
                  value={searchTerm}
                  onChange={e => setSearchTerm(e.target.value)}
                  placeholder="Search suppliers"
                  className="flex-1 min-w-0 bg-transparent outline-none text-sm text-text placeholder:text-neutral-500"
                />
              </div>
              <div className="flex gap-1.5 flex-1 min-w-[200px]">
                <select
                  value={sortKey}
                  onChange={e => setSortKey(e.target.value)}
                  aria-label="Sort by"
                  className="flex-1 min-w-0 h-[42px] px-2 rounded-md border border-divider bg-bg text-text text-[13px] focus:outline-none focus:border-accent"
                >
                  {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <button
                  onClick={() => setSortDesc(d => !d)}
                  className="shrink-0 h-[42px] px-3 rounded-md border border-divider text-[12.5px] text-text hover:border-accent transition-colors whitespace-nowrap flex items-center gap-1"
                >
                  <ArrowUpDown className="w-3.5 h-3.5" /> {sortDesc ? 'High → Low' : 'Low → High'}
                </button>
              </div>
            </div>

            {/* Supplier rows */}
            {sorted.length === 0 ? (
              <p className="text-sm text-neutral-600 text-center py-8">
                {searchTerm ? `No suppliers match "${searchTerm}".` : 'No suppliers found.'}
              </p>
            ) : sorted.map(c => {
              const bought = c.purchases > 0.005 ? `Bought ${fmtMoney(c.purchases)}` : null;
              const paid = c.payments > 0.005 ? `Paid ${fmtMoney(c.payments)}` : null;
              const returned = c.returns > 0.005 ? `Returned ${fmtMoney(c.returns)}` : null;
              const sub = [bought, paid, returned].filter(Boolean).join(' · ') || 'No activity';
              return (
                <button
                  key={c.supplier_name}
                  onClick={() => fetchLedger(c.supplier_name)}
                  className="w-full flex items-center gap-3 py-3 border-b border-divider last:border-0 text-left"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-[14.5px] truncate">{c.supplier_name}</div>
                    <div className="text-[11.5px] text-neutral-700 truncate">{sub}</div>
                  </div>
                  <span className="font-heading font-semibold text-[17px] whitespace-nowrap shrink-0">{fmtCr(c.period_closing)}</span>
                </button>
              );
            })}
          </>
        )}
      </div>

      {/* Supplier ledger bottom sheet */}
      {selectedSupplier && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={closeLedger} />
          <div className="fixed inset-x-0 bottom-0 sm:inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 pointer-events-none">
            <div
              className="bg-surface border-t sm:border border-divider rounded-t-2xl sm:rounded-lg shadow-lg w-full sm:max-w-lg max-h-[88vh] flex flex-col pointer-events-auto animate-in slide-in-from-bottom-full sm:zoom-in-95"
              onClick={e => e.stopPropagation()}
            >
              <div className="w-12 h-1.5 bg-divider rounded-full mx-auto mt-3 sm:hidden" />
              <div className="flex items-start justify-between gap-3 px-6 py-4">
                <div className="min-w-0">
                  <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">Supplier ledger · {periodLabel.toLowerCase()}</div>
                  <h3 className="font-heading font-semibold text-xl truncate">{selectedSupplier}</h3>
                </div>
                <button onClick={closeLedger} className="text-neutral-500 hover:text-text transition-colors shrink-0">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="overflow-y-auto flex-1">
                {ledgerLoading || !ledger ? (
                  <p className="text-sm text-neutral-600 text-center py-8">Loading&hellip;</p>
                ) : (
                  <>
                    <div className="grid grid-cols-3 border-t border-divider px-6">
                      {[
                        { k: 'Opening', v: fmtCr(ledger.period_opening) },
                        { k: 'Purchases', v: fmtMoney2(ledger.summary.purchases) },
                        { k: 'Returns', v: fmtMoney2(ledger.summary.returns) },
                        { k: 'Payments', v: fmtMoney2(ledger.summary.payments) },
                        { k: 'Adjustments', v: Math.abs(ledger.summary.other_adjustments) > 0.005 ? fmtMoney2(ledger.summary.other_adjustments) : '–' },
                        { k: 'Closing', v: fmtCr(ledger.period_closing), accent: true },
                      ].map((g, i) => (
                        <div
                          key={g.k}
                          className={`py-2 border-b border-divider ${i % 3 !== 0 ? 'border-l pl-3' : ''}`}
                        >
                          <div className="text-[11px] text-neutral-700">{g.k}</div>
                          <div className={`text-sm truncate ${g.accent ? 'text-accent-700 font-semibold' : ''}`}>{g.v}</div>
                        </div>
                      ))}
                    </div>

                    <div className="px-6 pt-4 pb-2">
                      <div className="flex justify-between text-[10.5px] tracking-[0.1em] uppercase text-neutral-700">
                        <span>Movements</span><span>Balance</span>
                      </div>

                      <div className="flex items-center gap-3 py-2.5 border-t border-divider">
                        <span className="w-10 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-semibold">Opening balance</div>
                          <div className="text-[11.5px] text-neutral-700">{periodLabel.toLowerCase()}</div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="text-sm text-neutral-700">{fmtCr(ledger.period_opening)}</div>
                        </div>
                      </div>

                      {ledger.movements.length === 0 && (
                        <p className="text-sm text-neutral-600 py-3 border-t border-divider">No movements in this period.</p>
                      )}

                      {ledger.movements.map((m, idx) => {
                        const ref = m.reference || m.voucher_number || (m.is_pending ? 'Pending Tally sync' : '—');
                        const isReduction = m.amount < 0;
                        const clickable = !!m.voucher_id && !m.is_pending;
                        return (
                          <button
                            key={idx}
                            onClick={() => openVoucher(m)}
                            disabled={!clickable}
                            title={m.is_pending ? "Not yet confirmed by Tally -- no details to view yet" : undefined}
                            className={`w-full flex items-center gap-3 py-2.5 border-t border-divider text-left ${clickable ? 'hover:bg-text/4' : 'opacity-70 cursor-default'}`}
                          >
                            <span className="w-10 shrink-0 text-xs text-neutral-700 leading-tight">{formatDateShort(m.date)}</span>
                            <div className="flex-1 min-w-0">
                              <div className="text-sm">{m.voucher_type}</div>
                              <div className="text-[11.5px] text-neutral-700 truncate">{ref}</div>
                            </div>
                            <div className="text-right shrink-0">
                              <div className={`text-sm ${isReduction ? 'text-accent-700' : 'text-text'}`}>
                                {isReduction ? '−' : '+'} {fmtMoney2(m.amount)}
                              </div>
                              <div className="text-[11.5px] text-neutral-700">{fmtCr(m.running_balance)}</div>
                            </div>
                          </button>
                        );
                      })}

                      <div className="flex items-center gap-3 py-2.5 border-t border-text">
                        <span className="w-10 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-semibold">Closing balance</div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="text-sm font-semibold">{fmtCr(ledger.period_closing)}</div>
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>

              <div className="flex gap-3 px-6 py-4 border-t border-divider">
                <button
                  onClick={closeLedger}
                  className="flex-1 h-11 border border-divider rounded-md text-[15px] hover:bg-text/5 transition-colors"
                >
                  Close
                </button>
                {ledger && ledger.period_closing > 0.005 && (
                  <button
                    onClick={() => router.push(`/payment?pay_to=${encodeURIComponent(selectedSupplier)}`)}
                    className="flex-[1.4] h-11 border border-accent text-accent-700 rounded-md text-[15px] font-semibold hover:bg-accent/12 active:bg-accent/22 transition-colors"
                  >
                    Record payment
                  </button>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* Voucher detail bottom sheet -- stacked above the ledger sheet */}
      {voucherModal && (
        <>
          <div className="fixed inset-0 bg-black/40 z-[60] transition-opacity" onClick={closeVoucher} />
          <div className="fixed inset-x-0 bottom-0 sm:inset-0 z-[61] flex items-end sm:items-center justify-center sm:p-4 pointer-events-none">
            <div
              className="bg-surface border-t sm:border border-divider rounded-t-2xl sm:rounded-lg shadow-lg w-full sm:max-w-lg max-h-[85vh] flex flex-col pointer-events-auto animate-in slide-in-from-bottom-full sm:zoom-in-95"
              onClick={e => e.stopPropagation()}
            >
              <div className="w-12 h-1.5 bg-divider rounded-full mx-auto mt-3 sm:hidden" />

              {voucherLoading || !voucherData ? (
                <div className="px-6 py-4">
                  <div className="flex items-center justify-between">
                    <h3 className="font-heading font-semibold text-xl">{voucherModal.type}</h3>
                    <button onClick={closeVoucher} className="text-neutral-500 hover:text-text transition-colors"><X className="w-5 h-5" /></button>
                  </div>
                  <p className="text-sm text-neutral-600 text-center py-8">Loading&hellip;</p>
                </div>
              ) : voucherModal.type === 'Purchase' ? (
                <>
                  <div className="flex items-start justify-between gap-3 px-6 py-4 border-b border-divider">
                    <div className="min-w-0">
                      <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">
                        Purchase bill{voucherData.voucher?.voucher_number ? ` · ${voucherData.voucher.voucher_number}` : ''}
                      </div>
                      <h3 className="font-heading font-semibold text-xl truncate">{voucherData.voucher?.party_ledger_name}</h3>
                      <p className="text-sm text-neutral-700">{formatDateFull(voucherData.voucher?.date)}</p>
                    </div>
                    <button onClick={closeVoucher} className="text-neutral-500 hover:text-text transition-colors shrink-0"><X className="w-5 h-5" /></button>
                  </div>

                  <div className="overflow-y-auto flex-1 px-6 py-4">
                    {voucherData.items && voucherData.items.length > 0 ? (
                      <>
                        <div className="border-t border-divider">
                          {voucherData.items.map((item, idx) => {
                            const qty = Math.abs(item.billed_qty || 0);
                            const rate = item.rate || (qty > 0 ? Math.abs(item.amount) / qty : 0);
                            return (
                              <div key={idx} className="flex justify-between gap-3 py-2.5 border-b border-divider">
                                <div className="min-w-0">
                                  <div className="text-sm truncate">{item.stock_item_name}</div>
                                  <div className="text-[11.5px] text-neutral-700">{qty} × {fmtMoney2(rate)}</div>
                                </div>
                                <div className="text-sm shrink-0">{fmtMoney2(Math.abs(item.amount))}</div>
                              </div>
                            );
                          })}
                        </div>

                        <div className="flex flex-col gap-1 mt-4">
                          <div className="flex justify-between text-sm text-neutral-700">
                            <span>Items total</span><span>{fmtMoney2(voucherData.purchase_value)}</span>
                          </div>
                          {voucherData.taxes?.cgst > 0 && (
                            <div className="flex justify-between text-sm text-neutral-700"><span>CGST</span><span>{fmtMoney2(voucherData.taxes.cgst)}</span></div>
                          )}
                          {voucherData.taxes?.sgst > 0 && (
                            <div className="flex justify-between text-sm text-neutral-700"><span>SGST</span><span>{fmtMoney2(voucherData.taxes.sgst)}</span></div>
                          )}
                          {voucherData.taxes?.igst > 0 && (
                            <div className="flex justify-between text-sm text-neutral-700"><span>IGST</span><span>{fmtMoney2(voucherData.taxes.igst)}</span></div>
                          )}
                          {Math.abs(voucherData.rounding || 0) > 0.005 && (
                            <div className="flex justify-between text-sm text-neutral-700"><span>Rounding off</span><span>{fmtMoney2(voucherData.rounding)}</span></div>
                          )}
                          <div className="flex justify-between items-baseline border-t border-text pt-2 mt-1">
                            <span className="font-heading font-semibold text-lg">Bill total</span>
                            <span className="font-heading font-semibold text-xl">{fmtMoney2(voucherData.supplier_payable)}</span>
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="text-center py-8 flex flex-col items-center gap-2 text-neutral-600">
                        <Package className="w-6 h-6" />
                        <p className="text-sm">No inventory items found for this bill.</p>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-start justify-between gap-3 px-6 py-4 border-b border-divider">
                    <div className="min-w-0">
                      <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">
                        {voucherData.voucher?.voucher_type}{voucherData.voucher?.voucher_number ? ` · ${voucherData.voucher.voucher_number}` : ''}
                      </div>
                      <h3 className="font-heading font-semibold text-xl truncate">{voucherData.voucher?.party_ledger_name || selectedSupplier}</h3>
                      <p className="text-sm text-neutral-700">{formatDateFull(voucherData.voucher?.date)}</p>
                    </div>
                    <button onClick={closeVoucher} className="text-neutral-500 hover:text-text transition-colors shrink-0"><X className="w-5 h-5" /></button>
                  </div>

                  <div className="overflow-y-auto flex-1 px-6 py-4">
                    {voucherData.voucher?.voucher_type === 'Payment' && (() => {
                      const other = (voucherData.ledgers || []).find(l => l.ledger_name !== voucherData.voucher?.party_ledger_name);
                      return other ? (
                        <p className="text-sm text-neutral-700 mb-4">Paid via <span className="text-text font-medium">{other.ledger_name}</span></p>
                      ) : null;
                    })()}

                    {voucherData.voucher?.narration && (
                      <p className="text-sm text-neutral-700 mb-4">{voucherData.voucher.narration}</p>
                    )}

                    {voucherData.inventory && voucherData.inventory.length > 0 && (
                      <div className="border-t border-divider mb-4">
                        {voucherData.inventory.map((item, idx) => {
                          const qty = Math.abs(item.billed_qty || 0);
                          const rate = qty > 0 ? Math.abs(item.amount) / qty : 0;
                          return (
                            <div key={idx} className="flex justify-between gap-3 py-2.5 border-b border-divider">
                              <div className="min-w-0">
                                <div className="text-sm truncate">{item.stock_item_name}</div>
                                <div className="text-[11.5px] text-neutral-700">{qty} × {fmtMoney2(rate)}</div>
                              </div>
                              <div className="text-sm shrink-0">{fmtMoney2(Math.abs(item.amount))}</div>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    <div className="flex flex-col gap-1">
                      <div className="flex justify-between text-[10.5px] tracking-[0.1em] uppercase text-neutral-700 mb-1">
                        <span>Ledger</span><span>Dr / Cr</span>
                      </div>
                      {(voucherData.ledgers || []).map((l, idx) => (
                        <div key={idx} className="flex justify-between text-sm py-1.5 border-t border-divider">
                          <span className="text-text">{l.ledger_name}</span>
                          <span className={l.is_deemed_positive === 1 ? 'text-text' : 'text-accent-700'}>
                            {l.is_deemed_positive === 1 ? 'Dr ' : 'Cr '}{fmtMoney2(l.amount)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}

              <div className="px-6 py-4 border-t border-divider">
                <button onClick={closeVoucher} className="w-full h-11 border border-divider rounded-md text-[15px] hover:bg-text/5 transition-colors">
                  Close
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
