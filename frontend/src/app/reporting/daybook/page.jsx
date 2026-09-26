"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import TopBar from '@/components/layout/TopBar';
import ReportTabs from '@/components/layout/ReportTabs';
import { Wifi, WifiOff, AlertTriangle, X, Package } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
// The daybook has no pagination UI in the new design -- it just renders one
// flowing day-grouped list, like the Sales/Purchases day-wise lists already
// do. A generous cap keeps that safe without a real page-fetch loop; a small
// shop's daybook for any of the period presets below is nowhere near this.
const FETCH_LIMIT = 1000;

const PRESETS = [
  { label: "Today", value: "today" },
  { label: "7 days", value: "7days" },
  { label: "This month", value: "month" },
  { label: "30 days", value: "30days" },
  { label: "Custom", value: "custom" },
];

function fmtMoney(v) {
  const n = Math.round(v || 0);
  return `₹${n.toLocaleString('en-IN')}`;
}
function fmtMoney2(v) {
  const n = Math.abs(Number(v || 0));
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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
function formatDateFull(yyyymmdd) {
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(toDate(yyyymmdd));
}
function dayHeaderLabel(yyyymmdd) {
  const d = toDate(yyyymmdd);
  const today = new Date();
  const yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  if (fmtYYYYMMDD(d) === fmtYYYYMMDD(today)) return 'Today';
  if (fmtYYYYMMDD(d) === fmtYYYYMMDD(yesterday)) return 'Yesterday';
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).format(d);
}
function formatTime(createdAt) {
  if (!createdAt) return null;
  const d = new Date(createdAt.endsWith('Z') ? createdAt : createdAt + 'Z');
  if (isNaN(d.getTime())) return null;
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

// The four primary voucher types this app actually distinguishes -- matches
// the buckets the rest of the reporting suite (Sales/Purchases reports) use.
const TOTAL_BUCKETS = [
  { key: 'sales', label: 'Sales' },
  { key: 'purchase', label: 'Purchases' },
  { key: 'receipt', label: 'Money in' },
  { key: 'payment', label: 'Money out' },
];

// v.debit/v.credit follow the ledger's own Dr/Cr convention (Purchase is a
// debit-normal account, Sales a credit-normal one), not cash direction, so
// they can't be used directly as "money in vs out" -- Payment (money out)
// and Sales (money in) are BOTH credits under that convention. Bucket by
// voucher type instead, matching the totals grid above.
function signedAmount(v) {
  const t = (v.voucher_type || '').toLowerCase();
  if (t === 'sales' || t === 'receipt') return v.amount;
  if (t === 'purchase' || t === 'payment') return -v.amount;
  if (t === 'debit note' || t === 'purchase return') return v.amount;
  if (t === 'credit note') return -v.amount;
  return v.credit >= v.debit ? v.amount : -v.amount;
}

export default function DaybookReport() {
  const { user } = useAuth();
  const { isOnline } = useSyncStatus();
  const lockedStore = user && !user.is_owner ? user.store_name : null;

  const [preset, setPreset] = useState("7days");
  const [customDates, setCustomDates] = useState(null);
  const [selectedStore, setSelectedStore] = useState(lockedStore || "");
  const [costCentres, setCostCentres] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [queueCount, setQueueCount] = useState(null);

  const [voucherModal, setVoucherModal] = useState(null); // { id, type }
  const [voucherData, setVoucherData] = useState(null);
  const [voucherLoading, setVoucherLoading] = useState(false);

  const dates = useMemo(() => getPresetDates(preset, customDates), [preset, customDates]);

  useEffect(() => {
    if (lockedStore) setSelectedStore(lockedStore);
  }, [lockedStore]);

  useEffect(() => {
    fetch(`${API_BASE}/api/reporting/inspect/cost-centres`)
      .then(res => res.json())
      .then(d => { if (Array.isArray(d)) setCostCentres(d); })
      .catch(err => console.error(err));

    fetch('/api/dashboard/stats')
      .then(res => res.json())
      .then(d => setQueueCount(d.queue_count ?? 0))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!dates.start || !dates.end) return;
    setLoading(true);
    const params = new URLSearchParams({ start_date: dates.start, end_date: dates.end, limit: FETCH_LIMIT, sort_by: 'date_desc' });
    if (selectedStore) params.append('cost_centre', selectedStore);

    fetch(`${API_BASE}/api/reporting/daybook?${params}`)
      .then(res => { if (!res.ok) throw new Error("Failed to fetch daybook"); return res.json(); })
      .then(d => { setData(d); setError(null); })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [dates, selectedStore]);

  const openVoucher = (v) => {
    if (v.is_pending || typeof v.id !== 'number') return;
    setVoucherModal({ id: v.id, type: v.voucher_type });
    setVoucherLoading(true);
    const url = v.voucher_type === 'Purchase'
      ? `${API_BASE}/api/reporting/purchases/bills/${v.id}`
      : `${API_BASE}/api/reporting/vouchers/${v.id}`;
    fetch(url)
      .then(res => { if (!res.ok) throw new Error("Failed to fetch voucher"); return res.json(); })
      .then(d => setVoucherData(d))
      .catch(() => setVoucherData(null))
      .finally(() => setVoucherLoading(false));
  };
  const closeVoucher = () => { setVoucherModal(null); setVoucherData(null); };

  const vouchers = data?.vouchers || [];

  const totals = useMemo(() => {
    const t = { sales: 0, purchase: 0, receipt: 0, payment: 0 };
    vouchers.forEach(v => {
      const key = (v.voucher_type || '').toLowerCase();
      if (key in t) t[key] += v.amount;
    });
    return t;
  }, [vouchers]);

  // Grouped by day, newest first -- each day's rows sorted with real-time
  // (pending, queued-but-not-synced) entries first since those are the only
  // ones with an actual time of day, then confirmed vouchers after.
  const dayGroups = useMemo(() => {
    const byDate = {};
    vouchers.forEach(v => {
      if (!byDate[v.date]) byDate[v.date] = [];
      byDate[v.date].push(v);
    });
    return Object.keys(byDate).sort((a, b) => b.localeCompare(a)).map(date => {
      const rows = [...byDate[date]].sort((a, b) => {
        if (a.is_pending !== b.is_pending) return a.is_pending ? -1 : 1;
        if (a.is_pending) return (b.created_at || '').localeCompare(a.created_at || '');
        return (typeof b.id === 'number' ? b.id : 0) - (typeof a.id === 'number' ? a.id : 0);
      });
      const net = rows.reduce((sum, v) => sum + signedAmount(v), 0);
      return { date, rows, net };
    });
  }, [vouchers]);

  const now = new Date();
  const monthKicker = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(now).toUpperCase();
  const storeLabel = selectedStore ? selectedStore.toUpperCase() : 'ALL STORES';
  const periodLabelMap = { today: 'TODAY', '7days': 'LAST 7 DAYS', month: 'THIS MONTH', '30days': 'LAST 30 DAYS' };
  const periodLabel = periodLabelMap[preset] || `${formatDateFull(dates.start)} – ${formatDateFull(dates.end)}`.toUpperCase();

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
        {!lockedStore && (
          <div className="flex gap-2 overflow-x-auto py-4 -mx-1 px-1" style={{ scrollbarWidth: 'none' }}>
            <button
              onClick={() => setSelectedStore('')}
              className={`shrink-0 px-3.5 h-9 rounded-full border text-sm transition-colors ${!selectedStore ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider text-text hover:border-text/45'}`}
            >
              All stores
            </button>
            {costCentres.map(c => (
              <button
                key={c.id}
                onClick={() => setSelectedStore(c.name)}
                className={`shrink-0 px-3.5 h-9 rounded-full border text-sm whitespace-nowrap transition-colors ${selectedStore === c.name ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider text-text hover:border-text/45'}`}
              >
                {c.name}
              </button>
            ))}
          </div>
        )}

        <div className="flex gap-2 overflow-x-auto pb-4 -mx-1 px-1" style={{ scrollbarWidth: 'none' }}>
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
            <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700 mb-3">
              {storeLabel} · {periodLabel} · {vouchers.length} entr{vouchers.length === 1 ? 'y' : 'ies'}
            </div>

            <div className="grid grid-cols-2 border-t border-divider">
              {TOTAL_BUCKETS.map((b, i) => (
                <div key={b.key} className={`py-3 border-b border-divider ${i % 2 === 1 ? 'border-l pl-4' : ''}`}>
                  <div className="text-sm text-neutral-700">{b.label}</div>
                  <div className="font-heading text-2xl mt-1">{fmtMoney(totals[b.key])}</div>
                </div>
              ))}
            </div>

            {dayGroups.length === 0 ? (
              <p className="text-sm text-neutral-600 text-center py-8">No transactions in this period.</p>
            ) : dayGroups.map(group => (
              <div key={group.date} className="mt-5">
                <div className="flex justify-between items-baseline text-[10.5px] tracking-[0.1em] uppercase text-neutral-700 pb-1">
                  <span>{dayHeaderLabel(group.date)}</span>
                  <span className="normal-case tracking-normal text-[13px]">
                    {group.rows.length} entr{group.rows.length === 1 ? 'y' : 'ies'} · net {group.net < 0 ? '−' : '+'} {fmtMoney(Math.abs(group.net))}
                  </span>
                </div>
                {group.rows.map((v, idx) => {
                  const signed = signedAmount(v);
                  const clickable = !v.is_pending && typeof v.id === 'number';
                  const time = v.is_pending ? formatTime(v.created_at) : null;
                  return (
                    <button
                      key={idx}
                      onClick={() => openVoucher(v)}
                      disabled={!clickable}
                      title={v.is_pending ? "Not yet confirmed by Tally -- no details to view yet" : undefined}
                      className={`w-full flex items-center gap-3 py-2.5 border-t border-divider text-left ${clickable ? 'hover:bg-text/4' : v.is_pending ? '' : 'cursor-default'}`}
                    >
                      <span className="w-11 shrink-0 text-xs text-neutral-700">{time || ''}</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-[14.5px] truncate">{v.party_ledger_name || 'Unknown'}</div>
                        <div className="text-[11.5px] text-neutral-700 truncate">
                          {v.voucher_type} · {v.store_display}
                          {v.is_pending && <span className="text-accent-700"> · Waiting for Tally</span>}
                        </div>
                      </div>
                      <span className={`font-heading font-semibold text-base whitespace-nowrap shrink-0 ${v.is_pending ? 'text-accent-700' : 'text-text'}`}>
                        {signed < 0 ? '−' : '+'} {fmtMoney(Math.abs(signed))}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))}
          </>
        )}
      </div>

      {/* Voucher detail bottom sheet */}
      {voucherModal && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={closeVoucher} />
          <div className="fixed inset-x-0 bottom-0 sm:inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 pointer-events-none">
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
                      <h3 className="font-heading font-semibold text-xl truncate">{voucherData.voucher?.party_ledger_name || 'Voucher'}</h3>
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

                    {voucherData.ledgers && voucherData.ledgers.length > 0 && (
                      <div className="flex flex-col gap-1">
                        <div className="flex justify-between text-[10.5px] tracking-[0.1em] uppercase text-neutral-700 mb-1">
                          <span>Ledger</span><span>Dr / Cr</span>
                        </div>
                        {voucherData.ledgers.map((l, idx) => (
                          <div key={idx} className="flex justify-between text-sm py-1.5 border-t border-divider">
                            <span className="text-text">{l.ledger_name}</span>
                            <span className={l.is_deemed_positive === 1 ? 'text-text' : 'text-accent-700'}>
                              {l.is_deemed_positive === 1 ? 'Dr ' : 'Cr '}{fmtMoney2(l.amount)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
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
