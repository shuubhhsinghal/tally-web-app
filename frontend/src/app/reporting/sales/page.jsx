"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import TopBar from '@/components/layout/TopBar';
import ReportTabs from '@/components/layout/ReportTabs';
import { Wifi, WifiOff, ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const PRESETS = [
  { label: "Today", value: "today" },
  { label: "7 days", value: "7days" },
  { label: "This month", value: "month" },
  { label: "30 days", value: "30days" },
  { label: "Custom", value: "custom" },
];

const MODE_FILTERS = [
  { label: "All", value: "all" },
  { label: "Cash", value: "cash" },
  { label: "UPI", value: "upi" },
  { label: "Credit", value: "credit" },
];

// Dark-to-light accent steps -- mirrors the reference's per-store bar/legend
// gradation (deep brown -> gold -> pale cream) using the shared accent ramp
// so it still reacts correctly to the light/dark theme toggle. Deliberately
// starts at 800, not 900 -- at swatch/bar-segment size 900 reads as near-black
// rather than brown, which breaks the "no black, warm gold family" palette.
const STORE_SWATCHES = ['bg-accent-800', 'bg-accent-500', 'bg-accent-300', 'bg-accent-600', 'bg-accent-200'];

function fmtMoney(v) {
  const n = Math.round(v || 0);
  return `₹${n.toLocaleString('en-IN')}`;
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
function formatDateWeekday(yyyymmdd) {
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).format(toDate(yyyymmdd));
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

export default function SalesReport() {
  const router = useRouter();
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
  const [highlightedDate, setHighlightedDate] = useState(null);
  const [expandedDay, setExpandedDay] = useState(null);
  const [modeFilter, setModeFilter] = useState('all');

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
    let url = `${API_BASE}/api/reporting/sales?start_date=${dates.start}&end_date=${dates.end}`;
    if (selectedStore) url += `&cost_centre=${encodeURIComponent(selectedStore)}`;

    fetch(url)
      .then(res => {
        if (!res.ok) throw new Error("Failed to fetch sales data");
        return res.json();
      })
      .then(d => {
        setData(d);
        setError(null);
        setHighlightedDate(null);
        const days = (d.trend || []).map(t => t.date).sort();
        const todayStr = fmtYYYYMMDD(new Date());
        setExpandedDay(days.includes(todayStr) ? todayStr : (days[days.length - 1] || null));
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [dates, selectedStore]);

  // Zero-filled so the chart draws one bar per calendar day in the period,
  // including days with no sales at all -- the API only ever returns rows
  // for dates that actually had a voucher.
  const trend = useMemo(() => {
    const byDate = {};
    (data?.trend || []).forEach(row => { byDate[row.date] = row; });
    const days = [];
    if (dates.start && dates.end) {
      let cursor = toDate(dates.start);
      const end = toDate(dates.end);
      while (cursor <= end) {
        const key = fmtYYYYMMDD(cursor);
        days.push(byDate[key] || { date: key, Combined: 0, Combined_invoices: 0, cash: 0, upi: 0, credit: 0 });
        cursor = new Date(cursor); cursor.setDate(cursor.getDate() + 1);
      }
    }
    return days;
  }, [data, dates]);
  const periodDays = daysBetween(dates.start, dates.end);
  const avgPerDay = data?.summary?.net_sales ? data.summary.net_sales / periodDays : 0;

  const storeNamesFromTrend = useMemo(() => {
    const names = new Set();
    trend.forEach(row => Object.keys(row).forEach(k => {
      if (!['date', 'Combined', 'Combined_invoices', 'cash', 'upi', 'credit'].includes(k)) names.add(k);
    }));
    return Array.from(names);
  }, [trend]);

  const maxBar = Math.max(1, ...trend.map(t => t.Combined || 0));
  const currentHighlight = trend.find(t => t.date === highlightedDate)
    || [...trend].reverse().find(t => t.Combined > 0)
    || trend[trend.length - 1];

  const storeComparison = data?.store_comparison?.filter(s => s.net_sales > 0).sort((a, b) => b.net_sales - a.net_sales) || [];

  // A single canonical store ordering (ranked by the "By store" totals, same
  // list the swatch colors there use) shared with the chart/legend below --
  // otherwise the same store could get two different swatch colors in the
  // two sections since the chart would otherwise order stores by whichever
  // date's trend row happened to mention them first.
  const storeNames = useMemo(() => {
    const primary = storeComparison.map(s => s.store_name);
    const extra = storeNamesFromTrend.filter(n => !primary.includes(n));
    return [...primary, ...extra];
  }, [storeComparison, storeNamesFromTrend]);
  const storeColorIndex = (name) => storeNames.indexOf(name) % STORE_SWATCHES.length;
  const totalForStores = storeComparison.reduce((sum, s) => sum + s.net_sales, 0);

  const dayRows = [...trend].reverse().filter(d => d.Combined > 0);
  const modeAmount = (row, mode) => mode === 'all' ? row.Combined : (row[mode] || 0);
  const maxModeAmount = Math.max(1, ...dayRows.map(r => modeAmount(r, modeFilter)));
  const totalForMode = dayRows.reduce((sum, r) => sum + modeAmount(r, modeFilter), 0);

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
        {/* Store filter pills */}
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

        {/* Period preset pills */}
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

            {(data?.unsynced_sales?.pending_amount || 0) - (data?.summary?.pending_amount || 0) > 0.005 && (
              <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
                <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                <p className="text-accent-700 text-xs leading-snug">
                  {fmtMoney(data.unsynced_sales.pending_amount - data.summary.pending_amount)} in sales has an uncertain Tally delivery status and is not included below &mdash; verify manually in the{' '}
                  <button onClick={() => router.push('/queue')} className="underline font-semibold">Queue</button> before it can be counted.
                </p>
              </div>
            )}

            {data?.unsynced_sales?.failed_count > 0 && (
              <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
                <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                <p className="text-accent-700 text-xs leading-snug">
                  {data.unsynced_sales.failed_count} sales entr{data.unsynced_sales.failed_count === 1 ? 'y' : 'ies'} totaling {fmtMoney(data.unsynced_sales.failed_amount)} failed to sync to Tally and need{data.unsynced_sales.failed_count === 1 ? 's' : ''} attention in the{' '}
                  <button onClick={() => router.push('/queue')} className="underline font-semibold">Queue</button>.
                </p>
              </div>
            )}

            {/* Summary */}
            <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">{storeLabel} · {periodLabel}</div>
            <div className="font-heading text-5xl leading-tight mt-1">{fmtMoney(data?.summary?.net_sales)}</div>
            <div className="text-sm text-neutral-700 mt-1.5">
              {data?.summary?.invoice_count || 0} bills &nbsp; Avg bill {fmtMoney(data?.summary?.avg_bill)}
              {data?.summary?.change_percentage !== null && data?.summary?.change_percentage !== undefined && (
                <>&nbsp; <ChangeIndicator pct={data.summary.change_percentage} /> vs previous {periodDays === 1 ? 'day' : `${periodDays} days`}</>
              )}
            </div>

            <div className="border-t border-divider my-5" />

            {/* Sales trend */}
            <div className="flex justify-between items-start mb-1">
              <div>
                <h3 className="font-heading font-semibold text-xl">Sales trend</h3>
                <p className="text-sm text-neutral-600">Daily &middot; {periodDays} day{periodDays === 1 ? '' : 's'}</p>
              </div>
              {currentHighlight && (
                <div className="text-right">
                  <div className="text-sm text-neutral-700">{formatDateWeekday(currentHighlight.date)}</div>
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
                    const hasSales = day.Combined > 0;
                    const isActive = hasSales && day.date === (currentHighlight?.date);
                    return (
                      <button
                        key={day.date}
                        onClick={() => hasSales && setHighlightedDate(day.date)}
                        disabled={!hasSales}
                        className={`flex-1 min-w-[6px] h-full flex flex-col-reverse rounded-t-sm overflow-hidden transition-opacity ${hasSales ? (isActive ? '' : 'opacity-55 hover:opacity-80') : 'cursor-default opacity-55'}`}
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
                    const pct = totalForStores > 0 ? (store.net_sales / totalForStores) * 100 : 0;
                    const swatch = STORE_SWATCHES[storeColorIndex(store.store_name)];
                    return (
                      <div key={store.store_name}>
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className={`w-2.5 h-2.5 rounded-sm shrink-0 ${swatch}`} />
                          <span className="text-[15px]">{store.store_name}</span>
                          <span className="flex-1" />
                          <ChangeIndicator pct={store.change_percentage} />
                          <span className="font-heading font-semibold text-lg w-24 text-right">{fmtMoney(store.net_sales)}</span>
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

            {/* Day-wise sales */}
            <div className="flex justify-between items-baseline mb-4">
              <h3 className="font-heading font-semibold text-xl">Day-wise sales</h3>
              <span className="text-sm text-neutral-700">Total {fmtMoney(totalForMode)}</span>
            </div>

            <div className="grid grid-cols-4 border border-divider rounded-md overflow-hidden mb-4">
              {MODE_FILTERS.map(m => (
                <button
                  key={m.value}
                  onClick={() => setModeFilter(m.value)}
                  className={`h-9 text-[13px] transition-colors ${modeFilter === m.value ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
                >
                  {m.label}
                </button>
              ))}
            </div>

            {dayRows.length === 0 ? (
              <p className="text-sm text-neutral-600 text-center py-8">No sales recorded for this period.</p>
            ) : dayRows.map(day => {
              const amount = modeAmount(day, modeFilter);
              const pct = maxModeAmount > 0 ? (amount / maxModeAmount) * 100 : 0;
              const isOpen = expandedDay === day.date;
              return (
                <div key={day.date} className="border-b border-divider last:border-0">
                  <button
                    onClick={() => setExpandedDay(isOpen ? null : day.date)}
                    className="w-full flex items-center gap-3 py-3.5 text-left"
                  >
                    <div className="w-24 shrink-0">
                      <div className="text-[15px]">{dayOfWeekLabel(day.date)}</div>
                      <div className="text-sm text-neutral-600">{day.Combined_invoices || 0} bill{day.Combined_invoices === 1 ? '' : 's'}</div>
                    </div>
                    <div className="flex-1 h-1.5 rounded-full bg-divider overflow-hidden">
                      <div className="h-full rounded-full bg-accent-600" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="font-heading font-semibold text-lg shrink-0">{fmtMoney(amount)}</span>
                    {isOpen ? <ChevronDown className="w-4 h-4 text-neutral-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-neutral-500 shrink-0" />}
                  </button>

                  {isOpen && (
                    <div className="pb-4 pl-8 flex flex-col gap-1">
                      {['cash', 'upi', 'credit'].map(mode => (
                        (day[mode] || 0) > 0 && (
                          <div key={mode} className="flex justify-between text-sm py-1">
                            <span className="text-neutral-700 capitalize">{mode}</span>
                            <span className="text-text">{fmtMoney(day[mode])}</span>
                          </div>
                        )
                      ))}
                      {storeNames.length > 1 && (
                        <div className="border-t border-divider mt-2 pt-2 flex flex-col gap-1">
                          {storeNames.map(name => (
                            (day[name] || 0) > 0 && (
                              <div key={name} className="flex justify-between text-sm py-1">
                                <span className="text-neutral-700">{name}</span>
                                <span className="text-text">{fmtMoney(day[name])}</span>
                              </div>
                            )
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}
