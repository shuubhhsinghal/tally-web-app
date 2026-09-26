"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import TopBar from '@/components/layout/TopBar';
import ReportTabs from '@/components/layout/ReportTabs';
import { Wifi, WifiOff, ChevronDown, ChevronRight, AlertTriangle, Calendar, Users, X, Package } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const PRESETS = [
  { label: "Today", value: "today" },
  { label: "7 days", value: "7days" },
  { label: "This month", value: "month" },
  { label: "30 days", value: "30days" },
  { label: "Custom", value: "custom" },
];

// Dark-to-light accent steps -- mirrors the Sales report's per-store bar/legend
// gradation so the two reports read as the same visual language.
const STORE_SWATCHES = ['bg-accent-800', 'bg-accent-500', 'bg-accent-300', 'bg-accent-600', 'bg-accent-200'];

function fmtMoney(v) {
  const n = Math.round(v || 0);
  return `₹${n.toLocaleString('en-IN')}`;
}
function fmtMoney2(v) {
  const n = Number(v || 0);
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pad2(n) { return String(n).padStart(2, '0'); }
function fmtYYYYMMDD(d) { return `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}`; }

function getPresetDates(preset, custom) {
  const today = new Date();
  const fmt = fmtYYYYMMDD;

  if (preset === "today") {
    return { start: fmt(today), end: fmt(today) };
  }
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
function dayOfWeekLabel(yyyymmdd) {
  const d = toDate(yyyymmdd);
  const today = new Date();
  const yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  if (fmtYYYYMMDD(d) === fmtYYYYMMDD(today)) return 'Today';
  if (fmtYYYYMMDD(d) === fmtYYYYMMDD(yesterday)) return 'Yesterday';
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).format(d);
}
function daysBetween(start, end) {
  const ms = toDate(end) - toDate(start);
  return Math.round(ms / 86400000) + 1;
}

function ChangeIndicator({ pct }) {
  if (pct === null || pct === undefined) return null;
  const arrow = pct >= 0 ? '▲' : '▼';
  return <span className="text-neutral-700">{arrow} {Math.abs(pct).toFixed(0)}%</span>;
}

export default function PurchasesReport() {
  const router = useRouter();
  const { user } = useAuth();
  const { isOnline } = useSyncStatus();
  const isOwner = !user || user.is_owner;
  const lockedStore = user && !user.is_owner ? user.store_name : null;

  const [preset, setPreset] = useState("7days");
  const [customDates, setCustomDates] = useState(null);
  const [selectedStore, setSelectedStore] = useState(lockedStore || "");
  const [costCentres, setCostCentres] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [queueCount, setQueueCount] = useState(null);

  const [listMode, setListMode] = useState('daily'); // daily | suppliers
  const [expandedRow, setExpandedRow] = useState(null); // day date, or supplier name
  const [rowBills, setRowBills] = useState([]);
  const [rowBillsLoading, setRowBillsLoading] = useState(false);

  const [supplierData, setSupplierData] = useState([]);
  const [suppliersLoading, setSuppliersLoading] = useState(false);

  const [billDetails, setBillDetails] = useState(null);
  const [billDetailsLoading, setBillDetailsLoading] = useState(false);

  // A running balance as of today -- independent of the period pills above,
  // same way the reference design's "Still to pay" figure doesn't move when
  // you change the trend period. Owner-only, mirroring the Creditors report's
  // own access restriction (a supplier balance isn't scoped to one store).
  const [stillToPay, setStillToPay] = useState(null);
  const [stillToPayLoading, setStillToPayLoading] = useState(false);

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
    if (!isOwner) return;
    setStillToPayLoading(true);
    const today = fmtYYYYMMDD(new Date());
    fetch(`${API_BASE}/api/reporting/creditors?start_date=20000101&end_date=${today}`)
      .then(res => { if (!res.ok) throw new Error("Failed to fetch creditors"); return res.json(); })
      .then(d => {
        const due = (d.creditors || []).filter(c => c.period_closing > 0.005);
        const total = due.reduce((sum, c) => sum + c.period_closing, 0);
        setStillToPay({ total, supplierCount: due.length });
      })
      .catch(() => setStillToPay(null))
      .finally(() => setStillToPayLoading(false));
  }, [isOwner]);

  useEffect(() => {
    if (!dates.start || !dates.end) return;
    setLoading(true);
    setExpandedRow(null);
    let url = `${API_BASE}/api/reporting/purchases?start_date=${dates.start}&end_date=${dates.end}`;
    if (selectedStore) url += `&cost_centre=${encodeURIComponent(selectedStore)}`;

    fetch(url)
      .then(res => { if (!res.ok) throw new Error("Failed to fetch purchases data"); return res.json(); })
      .then(d => { setData(d); setError(null); })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [dates, selectedStore]);

  useEffect(() => {
    if (!dates.start || !dates.end) return;
    setSuppliersLoading(true);
    let url = `${API_BASE}/api/reporting/purchases/suppliers?start_date=${dates.start}&end_date=${dates.end}&limit=100`;
    if (selectedStore) url += `&cost_centre=${encodeURIComponent(selectedStore)}`;

    fetch(url)
      .then(res => res.json())
      .then(d => setSupplierData(d.suppliers || []))
      .catch(err => console.error(err))
      .finally(() => setSuppliersLoading(false));
  }, [dates, selectedStore]);

  useEffect(() => {
    if (!expandedRow) { setRowBills([]); return; }
    setRowBillsLoading(true);
    let url;
    if (listMode === 'daily') {
      url = `${API_BASE}/api/reporting/purchases/bills?start_date=${expandedRow}&end_date=${expandedRow}&limit=100`;
    } else {
      url = `${API_BASE}/api/reporting/purchases/bills?start_date=${dates.start}&end_date=${dates.end}&supplier_name=${encodeURIComponent(expandedRow)}&limit=100`;
    }
    if (selectedStore) url += `&cost_centre=${encodeURIComponent(selectedStore)}`;

    fetch(url)
      .then(res => res.json())
      .then(d => setRowBills(d.bills || []))
      .catch(err => console.error(err))
      .finally(() => setRowBillsLoading(false));
  }, [expandedRow, listMode, dates, selectedStore]);

  const handleFetchBillDetails = (voucherId) => {
    setBillDetailsLoading(true);
    fetch(`${API_BASE}/api/reporting/purchases/bills/${voucherId}`)
      .then(res => res.json())
      .then(d => setBillDetails(d))
      .catch(err => console.error(err))
      .finally(() => setBillDetailsLoading(false));
  };

  const toggleRow = (key) => setExpandedRow(prev => (prev === key ? null : key));
  const handleListModeChange = (mode) => { setListMode(mode); setExpandedRow(null); };

  // Zero-filled so the chart draws one bar per calendar day in the period,
  // same convention as the Sales report's trend.
  const trend = useMemo(() => {
    const byDate = {};
    (data?.trend || []).forEach(row => { byDate[row.date] = row; });
    const days = [];
    if (dates.start && dates.end) {
      let cursor = toDate(dates.start);
      const end = toDate(dates.end);
      while (cursor <= end) {
        const key = fmtYYYYMMDD(cursor);
        days.push(byDate[key] || { date: key, Combined: 0, Combined_invoices: 0 });
        cursor = new Date(cursor); cursor.setDate(cursor.getDate() + 1);
      }
    }
    return days;
  }, [data, dates]);
  const periodDays = daysBetween(dates.start, dates.end);
  const avgPerDay = data?.summary?.net_purchases ? data.summary.net_purchases / periodDays : 0;

  const storeNamesFromTrend = useMemo(() => {
    const names = new Set();
    trend.forEach(row => Object.keys(row).forEach(k => {
      if (!['date', 'Combined', 'Combined_invoices'].includes(k)) names.add(k);
    }));
    return Array.from(names);
  }, [trend]);

  const maxBar = Math.max(1, ...trend.map(t => t.Combined || 0));
  const [highlightedDate, setHighlightedDate] = useState(null);
  const currentHighlight = trend.find(t => t.date === highlightedDate)
    || [...trend].reverse().find(t => t.Combined > 0)
    || trend[trend.length - 1];

  const storeComparison = data?.store_comparison?.filter(s => s.net_purchases > 0).sort((a, b) => b.net_purchases - a.net_purchases) || [];
  const storeNames = useMemo(() => {
    const primary = storeComparison.map(s => s.store_name);
    const extra = storeNamesFromTrend.filter(n => !primary.includes(n));
    return [...primary, ...extra];
  }, [storeComparison, storeNamesFromTrend]);
  const storeColorIndex = (name) => storeNames.indexOf(name) % STORE_SWATCHES.length;
  const totalForStores = storeComparison.reduce((sum, s) => sum + s.net_purchases, 0);

  const dayRows = [...trend].reverse().filter(d => d.Combined > 0);

  const now = new Date();
  const monthKicker = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(now).toUpperCase();
  const storeLabel = selectedStore ? selectedStore.toUpperCase() : 'ALL STORES';
  const periodLabelMap = { today: 'TODAY', '7days': 'LAST 7 DAYS', month: 'THIS MONTH', '30days': 'LAST 30 DAYS' };
  const periodLabel = periodLabelMap[preset] || `${formatDateShort(dates.start)} – ${formatDateShort(dates.end)}`.toUpperCase();

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
            {data?.is_data_complete === false && (
              <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
                <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                <p className="text-accent-700 text-xs leading-snug">
                  Reporting data for this period may be incomplete &mdash; run a Tally sync to make sure everything is up to date.
                </p>
              </div>
            )}

            {(data?.unsynced_purchases?.pending_amount || 0) - (data?.summary?.pending_amount || 0) > 0.005 && (
              <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
                <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                <p className="text-accent-700 text-xs leading-snug">
                  {fmtMoney(data.unsynced_purchases.pending_amount - data.summary.pending_amount)} in purchases has an uncertain Tally delivery status and is not included below &mdash; verify manually in the{' '}
                  <button onClick={() => router.push('/queue')} className="underline font-semibold">Queue</button> before it can be counted.
                </p>
              </div>
            )}

            {data?.unsynced_purchases?.failed_count > 0 && (
              <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
                <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                <p className="text-accent-700 text-xs leading-snug">
                  {data.unsynced_purchases.failed_count} purchase entr{data.unsynced_purchases.failed_count === 1 ? 'y' : 'ies'} totaling {fmtMoney(data.unsynced_purchases.failed_amount)} failed to sync to Tally and need{data.unsynced_purchases.failed_count === 1 ? 's' : ''} attention in the{' '}
                  <button onClick={() => router.push('/queue')} className="underline font-semibold">Queue</button>.
                </p>
              </div>
            )}

            {/* Summary: Total purchases + Still to pay */}
            <div className={`grid ${isOwner ? 'grid-cols-[1.3fr_1fr]' : 'grid-cols-1'} gap-4 pb-4`}>
              <div className="min-w-0">
                <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">{storeLabel} · {periodLabel}</div>
                <div className="font-heading text-4xl leading-tight mt-1">{fmtMoney(data?.summary?.net_purchases)}</div>
                <div className="text-sm text-neutral-700 mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
                  <span>{data?.summary?.change_pct !== null && data?.summary?.change_pct !== undefined
                    ? `${data.summary.change_pct >= 0 ? '+' : ''}${data.summary.change_pct.toFixed(0)}% vs last period`
                    : ''}</span>
                </div>
                {Math.abs(data?.summary?.pending_amount || 0) > 0.005 && (
                  <p className="text-xs text-accent-700 mt-1">
                    {fmtMoney(data.summary.net_purchases_confirmed)} confirmed {data.summary.pending_amount >= 0 ? '+' : '-'} {fmtMoney(Math.abs(data.summary.pending_amount))} pending
                  </p>
                )}
              </div>

              {isOwner && (
                <div className="border-l border-divider pl-4 min-w-0">
                  <div className="text-[10.5px] tracking-[0.12em] uppercase text-neutral-700">Still to pay</div>
                  <div className="font-heading text-2xl leading-tight mt-1.5">
                    {stillToPayLoading ? '…' : fmtMoney(stillToPay?.total)}
                  </div>
                  <div className="text-xs text-neutral-700">
                    {stillToPayLoading ? '' : `${stillToPay?.supplierCount || 0} supplier${stillToPay?.supplierCount === 1 ? '' : 's'} · all stores`}
                  </div>
                </div>
              )}
            </div>

            <div className="border-t border-divider my-5" />

            {/* Purchase trend */}
            <div className="flex justify-between items-start mb-1">
              <div>
                <h3 className="font-heading font-semibold text-xl">Purchase trend</h3>
                <p className="text-sm text-neutral-600">Daily &middot; {periodDays} day{periodDays === 1 ? '' : 's'}</p>
              </div>
              {currentHighlight && (
                <div className="text-right">
                  <div className="text-sm text-neutral-700">{formatDateShort(currentHighlight.date)}</div>
                  <div className="font-heading font-semibold text-xl">{fmtMoney(currentHighlight.Combined)}</div>
                </div>
              )}
            </div>

            {trend.length > 0 && (
              <div className="mt-4">
                <div className="text-xs text-neutral-600 mb-1">avg {fmtMoney(avgPerDay)}</div>
                <div className="relative h-40 flex items-end gap-1.5 border-b border-divider">
                  <div
                    className="absolute left-0 right-0 border-t border-dashed border-neutral-500"
                    style={{ bottom: `${Math.min(100, (avgPerDay / maxBar) * 100)}%` }}
                  />
                  {trend.map(day => {
                    const hasPurchases = day.Combined > 0;
                    const isActive = hasPurchases && day.date === (currentHighlight?.date);
                    return (
                      <button
                        key={day.date}
                        onClick={() => hasPurchases && setHighlightedDate(day.date)}
                        disabled={!hasPurchases}
                        className={`flex-1 min-w-[6px] h-full flex flex-col-reverse rounded-t-sm overflow-hidden transition-opacity ${hasPurchases ? (isActive ? '' : 'opacity-55 hover:opacity-80') : 'cursor-default opacity-55'}`}
                        title={`${formatDateShort(day.date)}: ${fmtMoney(day.Combined)}`}
                      >
                        {storeNames.map((name, i) => {
                          const val = day[name] || 0;
                          const pct = maxBar > 0 ? (val / maxBar) * 100 : 0;
                          return pct > 0 ? (
                            <div key={name} className={STORE_SWATCHES[i % STORE_SWATCHES.length]} style={{ height: `${pct}%` }} />
                          ) : null;
                        })}
                      </button>
                    );
                  })}
                </div>
                <div className="flex justify-between text-xs text-neutral-600 mt-2">
                  <span>{formatDateShort(trend[0].date)}</span>
                  {trend.length > 2 && <span>{formatDateShort(trend[Math.floor(trend.length / 2)].date)}</span>}
                  <span>{formatDateShort(trend[trend.length - 1].date)}</span>
                </div>
                {storeNames.length > 0 && (
                  <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-4">
                    {storeNames.map((name, i) => (
                      <div key={name} className="flex items-center gap-1.5 text-sm text-neutral-700">
                        <span className={`w-2.5 h-2.5 rounded-sm shrink-0 ${STORE_SWATCHES[i % STORE_SWATCHES.length]}`} />
                        {name}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="border-t border-divider my-5" />

            {/* By store */}
            {storeComparison.length > 0 && !selectedStore && (
              <>
                <h3 className="font-heading font-semibold text-xl mb-4">By store</h3>
                <div className="flex flex-col gap-5">
                  {storeComparison.map((store) => {
                    const pct = totalForStores > 0 ? (store.net_purchases / totalForStores) * 100 : 0;
                    const swatch = STORE_SWATCHES[storeColorIndex(store.store_name)];
                    return (
                      <div key={store.store_name}>
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className={`w-2.5 h-2.5 rounded-sm shrink-0 ${swatch}`} />
                          <span className="text-[15px]">{store.store_name}</span>
                          <span className="flex-1" />
                          <span className="font-heading font-semibold text-lg">{fmtMoney(store.net_purchases)}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-1.5 rounded-full bg-divider overflow-hidden">
                            <div className={`h-full rounded-full ${swatch}`} style={{ width: `${pct}%` }} />
                          </div>
                          <span className="text-xs text-neutral-600 w-9 text-right">{pct.toFixed(0)}%</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="border-t border-divider my-5" />
              </>
            )}

            {/* Day-wise / Supplier-wise toggle */}
            <div className="grid grid-cols-2 border border-divider rounded-md overflow-hidden mb-4">
              <button
                onClick={() => handleListModeChange('daily')}
                className={`h-10 flex items-center justify-center gap-1.5 text-[13.5px] transition-colors ${listMode === 'daily' ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
              >
                <Calendar className="w-3.5 h-3.5" /> Day-wise
              </button>
              <button
                onClick={() => handleListModeChange('suppliers')}
                className={`h-10 flex items-center justify-center gap-1.5 text-[13.5px] transition-colors ${listMode === 'suppliers' ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
              >
                <Users className="w-3.5 h-3.5" /> Supplier-wise
              </button>
            </div>

            {listMode === 'daily' ? (
              dayRows.length === 0 ? (
                <p className="text-sm text-neutral-600 text-center py-8">No purchases in this period.</p>
              ) : dayRows.map(day => {
                const isOpen = expandedRow === day.date;
                return (
                  <div key={day.date} className="border-b border-divider last:border-0">
                    <button onClick={() => toggleRow(day.date)} className="w-full flex items-center gap-3 py-3.5 text-left">
                      <div className="flex-1 min-w-0">
                        <div className="text-[14.5px] truncate">{dayOfWeekLabel(day.date)}</div>
                        <div className="text-[11.5px] text-neutral-700">{day.Combined_invoices || 0} bill{day.Combined_invoices === 1 ? '' : 's'}</div>
                      </div>
                      <span className="font-heading font-semibold text-lg shrink-0">{fmtMoney(day.Combined)}</span>
                      {isOpen ? <ChevronDown className="w-4 h-4 text-neutral-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-neutral-500 shrink-0" />}
                    </button>
                    {isOpen && (
                      <div className="pl-3.5 border-l border-accent ml-1 mb-3 flex flex-col animate-in fade-in duration-200">
                        {rowBillsLoading ? (
                          <p className="text-sm text-neutral-600 py-3">Loading&hellip;</p>
                        ) : rowBills.length === 0 ? (
                          <p className="text-sm text-neutral-600 py-3">No bills found.</p>
                        ) : rowBills.map((bill, idx) => (
                          <button
                            key={idx}
                            onClick={() => !bill.is_pending && !String(bill.voucher_id).startsWith('pending-') && handleFetchBillDetails(bill.voucher_id)}
                            title={bill.is_pending ? "Not yet confirmed by Tally -- no details to view yet" : undefined}
                            className={`flex items-center gap-3 py-2.5 border-t border-divider text-left ${bill.is_pending ? 'opacity-70 cursor-default' : 'hover:bg-text/4'}`}
                          >
                            <div className="flex-1 min-w-0">
                              <div className="text-sm truncate">{bill.supplier_name}</div>
                              <div className="text-[11.5px] text-neutral-700">{bill.voucher_number || 'No ref'}</div>
                            </div>
                            <div className="text-right shrink-0">
                              <div className="font-heading font-semibold text-base">{fmtMoney2(bill.net_purchases)}</div>
                              {bill.is_pending && (
                                <span className="inline-block text-[10px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 border border-accent px-1.5 py-0.5 rounded mt-0.5">
                                  Pending
                                </span>
                              )}
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })
            ) : (
              suppliersLoading ? (
                <p className="text-sm text-neutral-600 text-center py-8">Loading&hellip;</p>
              ) : supplierData.length === 0 ? (
                <p className="text-sm text-neutral-600 text-center py-8">No suppliers found for this period.</p>
              ) : supplierData.map(supp => {
                const isOpen = expandedRow === supp.supplier_name;
                return (
                  <div key={supp.supplier_name} className="border-b border-divider last:border-0">
                    <button onClick={() => toggleRow(supp.supplier_name)} className="w-full flex items-center gap-3 py-3.5 text-left">
                      <div className="flex-1 min-w-0">
                        <div className="text-[14.5px] truncate">{supp.supplier_name}</div>
                        <div className="text-[11.5px] text-neutral-700">{supp.vouchers_count} bill{supp.vouchers_count === 1 ? '' : 's'}</div>
                      </div>
                      <span className="font-heading font-semibold text-lg shrink-0">{fmtMoney(supp.net_purchases)}</span>
                      {isOpen ? <ChevronDown className="w-4 h-4 text-neutral-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-neutral-500 shrink-0" />}
                    </button>
                    {isOpen && (
                      <div className="pl-3.5 border-l border-accent ml-1 mb-3 flex flex-col animate-in fade-in duration-200">
                        {rowBillsLoading ? (
                          <p className="text-sm text-neutral-600 py-3">Loading&hellip;</p>
                        ) : rowBills.length === 0 ? (
                          <p className="text-sm text-neutral-600 py-3">No bills found.</p>
                        ) : rowBills.map((bill, idx) => (
                          <button
                            key={idx}
                            onClick={() => !bill.is_pending && !String(bill.voucher_id).startsWith('pending-') && handleFetchBillDetails(bill.voucher_id)}
                            title={bill.is_pending ? "Not yet confirmed by Tally -- no details to view yet" : undefined}
                            className={`flex items-center gap-3 py-2.5 border-t border-divider text-left ${bill.is_pending ? 'opacity-70 cursor-default' : 'hover:bg-text/4'}`}
                          >
                            <div className="flex-1 min-w-0">
                              <div className="text-sm truncate">{formatDateFull(bill.date)}</div>
                              <div className="text-[11.5px] text-neutral-700">{bill.voucher_number || 'No ref'}</div>
                            </div>
                            <div className="text-right shrink-0">
                              <div className="font-heading font-semibold text-base">{fmtMoney2(bill.net_purchases)}</div>
                              {bill.is_pending && (
                                <span className="inline-block text-[10px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 border border-accent px-1.5 py-0.5 rounded mt-0.5">
                                  Pending
                                </span>
                              )}
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </>
        )}
      </div>

      {/* Bill details bottom sheet */}
      {billDetails && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={() => setBillDetails(null)} />
          <div className="fixed inset-x-0 bottom-0 sm:inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 pointer-events-none">
            <div
              className="bg-surface border-t sm:border border-divider rounded-t-2xl sm:rounded-lg shadow-lg w-full sm:max-w-lg max-h-[85vh] flex flex-col pointer-events-auto animate-in slide-in-from-bottom-full sm:zoom-in-95"
              onClick={e => e.stopPropagation()}
            >
              <div className="w-12 h-1.5 bg-divider rounded-full mx-auto mt-3 sm:hidden" />
              <div className="flex items-start justify-between gap-3 px-6 py-4 border-b border-divider">
                <div className="min-w-0">
                  <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">
                    Purchase bill{billDetails.voucher?.voucher_number ? ` · ${billDetails.voucher.voucher_number}` : ''}
                  </div>
                  <h3 className="font-heading font-semibold text-xl truncate">{billDetails.voucher?.party_ledger_name}</h3>
                  <p className="text-sm text-neutral-700">
                    {formatDateFull(billDetails.voucher?.date)}{billDetails.voucher?.store_name ? ` · ${billDetails.voucher.store_name}` : ''}
                  </p>
                </div>
                <button onClick={() => setBillDetails(null)} className="text-neutral-500 hover:text-text transition-colors shrink-0">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="overflow-y-auto flex-1 px-6 py-4">
                {billDetailsLoading ? (
                  <p className="text-sm text-neutral-600 text-center py-8">Loading&hellip;</p>
                ) : billDetails.items && billDetails.items.length > 0 ? (
                  <>
                    <div className="border-t border-divider">
                      {billDetails.items.map((item, idx) => {
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
                        <span>Items total</span>
                        <span>{fmtMoney2(billDetails.purchase_value)}</span>
                      </div>
                      {billDetails.taxes?.cgst > 0 && (
                        <div className="flex justify-between text-sm text-neutral-700">
                          <span>CGST</span><span>{fmtMoney2(billDetails.taxes.cgst)}</span>
                        </div>
                      )}
                      {billDetails.taxes?.sgst > 0 && (
                        <div className="flex justify-between text-sm text-neutral-700">
                          <span>SGST</span><span>{fmtMoney2(billDetails.taxes.sgst)}</span>
                        </div>
                      )}
                      {billDetails.taxes?.igst > 0 && (
                        <div className="flex justify-between text-sm text-neutral-700">
                          <span>IGST</span><span>{fmtMoney2(billDetails.taxes.igst)}</span>
                        </div>
                      )}
                      {Math.abs(billDetails.rounding || 0) > 0.005 && (
                        <div className="flex justify-between text-sm text-neutral-700">
                          <span>Rounding off</span><span>{fmtMoney2(billDetails.rounding)}</span>
                        </div>
                      )}
                      <div className="flex justify-between items-baseline border-t border-text pt-2 mt-1">
                        <span className="font-heading font-semibold text-lg">Bill total</span>
                        <span className="font-heading font-semibold text-xl">{fmtMoney2(billDetails.supplier_payable)}</span>
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

              <div className="px-6 py-4 border-t border-divider">
                <button
                  onClick={() => setBillDetails(null)}
                  className="w-full h-11 border border-divider rounded-md text-[15px] hover:bg-text/5 transition-colors"
                >
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
