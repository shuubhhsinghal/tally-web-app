"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import TopBar from '@/components/layout/TopBar';
import ReportTabs from '@/components/layout/ReportTabs';
import { Wifi, WifiOff, AlertTriangle } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

// The backend only ever computes P&L for these three fixed stores (see
// calculate_pl_for_store in reporting_pl.py) -- unlike Sales/Purchases/
// Daybook, this isn't driven by the dynamic cost-centres list.
const PL_STORES = ['Mahagun', 'Gulshan', 'Vvip'];

const PRESETS = [
  { label: "This month", value: "month" },
  { label: "This quarter", value: "quarter" },
  { label: "This financial year", value: "fy" },
  { label: "Custom months", value: "custom" },
];

function fmtMoney(v) {
  if (v === undefined || v === null) return "—";
  const n = Math.round(v);
  return `₹${Math.abs(n).toLocaleString('en-IN')}`;
}
function fmtMoney2(v) {
  if (v === undefined || v === null) return "—";
  return `₹${Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function getPresetMonths(preset) {
  const today = new Date();
  const y = today.getFullYear();
  const m = today.getMonth();
  const fmt = (year, monthIdx) => `${year}-${String(monthIdx + 1).padStart(2, '0')}`;

  if (preset === "quarter") {
    const qMonth = Math.floor(m / 3) * 3;
    return { start_month: fmt(y, qMonth), end_month: fmt(y, qMonth + 2) };
  }
  if (preset === "fy") {
    const fyStartYear = m >= 3 ? y : y - 1;
    return { start_month: fmt(fyStartYear, 3), end_month: fmt(fyStartYear + 1, 2) };
  }
  return { start_month: fmt(y, m), end_month: fmt(y, m) };
}

function monthLabel(yyyy_mm, withYear = true) {
  if (!yyyy_mm) return '';
  const [y, m] = yyyy_mm.split('-').map(Number);
  const d = new Date(y, m - 1, 1);
  return new Intl.DateTimeFormat('en-GB', { month: 'short', ...(withYear ? { year: 'numeric' } : {}) }).format(d);
}
function monthEnd(yyyy_mm) {
  const [y, m] = yyyy_mm.split('-').map(Number);
  return new Date(y, m, 0).getDate();
}

function ChangeIndicator({ pct }) {
  if (pct === null || pct === undefined) return null;
  const arrow = pct >= 0 ? '▲' : '▼';
  return <span className={pct >= 0 ? 'text-accent-700' : 'text-neutral-700'}>{arrow} {Math.abs(pct).toFixed(1)}% vs prev period</span>;
}

function changePct(value, prev) {
  if (value === null || value === undefined || prev === null || prev === undefined) return null;
  if (prev === 0) return value > 0 ? 100 : value < 0 ? -100 : 0;
  return ((value - prev) / Math.abs(prev)) * 100;
}

export default function PLReport() {
  const { user } = useAuth();
  const { isOnline } = useSyncStatus();
  const lockedStore = user && !user.is_owner ? user.store_name : null;

  const [preset, setPreset] = useState("month");
  const [months, setMonths] = useState(getPresetMonths("month"));
  const [selectedStore, setSelectedStore] = useState(lockedStore || "");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [queueCount, setQueueCount] = useState(null);

  useEffect(() => {
    if (lockedStore) setSelectedStore(lockedStore);
  }, [lockedStore]);

  useEffect(() => {
    fetch('/api/dashboard/stats')
      .then(res => res.json())
      .then(d => setQueueCount(d.queue_count ?? 0))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!months.start_month || !months.end_month) return;
    setLoading(true);
    fetch(`${API_BASE}/api/reporting/profit-loss?start_month=${months.start_month}&end_month=${months.end_month}`)
      .then(res => res.json().then(json => ({ ok: res.ok, json })))
      .then(({ ok, json }) => {
        if (!ok) throw new Error(json.detail || "Failed to fetch P&L report");
        setData(json);
        setError(null);
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [months]);

  const handlePreset = (p) => {
    setPreset(p);
    if (p !== "custom") setMonths(getPresetMonths(p));
  };

  // A staff account's response is a different, single-store shape
  // ({current, previous}) with no stores/combined/unallocated keys at all.
  const activeData = useMemo(() => {
    if (!data) return null;
    if (lockedStore) return data.current;
    if (!selectedStore) return data.combined;
    if (selectedStore === "Unallocated") return data.unallocated;
    return data.stores.find(s => s.store === selectedStore);
  }, [data, lockedStore, selectedStore]);

  const activePrevData = useMemo(() => {
    if (!data || !data.previous_period.is_data_complete) return null;
    if (lockedStore) return data.previous;
    if (!selectedStore) return data.combined_previous;
    if (selectedStore === "Unallocated") return data.unallocated_previous;
    return data.stores_previous.find(s => s.store === selectedStore);
  }, [data, lockedStore, selectedStore]);

  const missingStores = activeData?.missing_stores || [];

  const now = new Date();
  const monthKicker = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(now).toUpperCase();
  const storeLabel = selectedStore ? selectedStore.toUpperCase() : 'ALL STORES';
  const storeLabelStatement = selectedStore ? selectedStore : 'All stores combined';
  const periodLabelMap = { month: 'THIS MONTH', quarter: 'THIS QUARTER', fy: 'THIS FINANCIAL YEAR' };
  const periodLabel = periodLabelMap[preset] || `${monthLabel(months.start_month, false)} – ${monthLabel(months.end_month)}`.toUpperCase();
  const dateRangeFull = months.start_month && months.end_month
    ? `1 ${monthLabel(months.start_month, false)} – ${monthEnd(months.end_month)} ${monthLabel(months.end_month)}`
    : '';

  const netSalesChange = changePct(activeData?.revenue?.net_sales, activePrevData?.revenue?.net_sales);
  const grossProfitChange = changePct(activeData?.gross_profit, activePrevData?.gross_profit);
  const netProfitChange = changePct(activeData?.net_profit, activePrevData?.net_profit);
  const grossMargin = activeData?.gross_profit != null && activeData?.revenue?.net_sales ? (activeData.gross_profit / activeData.revenue.net_sales) * 100 : null;
  const netMargin = activeData?.net_profit != null && activeData?.revenue?.net_sales ? (activeData.net_profit / activeData.revenue.net_sales) * 100 : null;

  const storeCards = useMemo(() => {
    if (!data || lockedStore || selectedStore) return [];
    return PL_STORES.map(name => data.stores.find(s => s.store === name)).filter(Boolean);
  }, [data, lockedStore, selectedStore]);

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
            {PL_STORES.map(name => (
              <button
                key={name}
                onClick={() => setSelectedStore(name)}
                className={`shrink-0 px-3.5 h-9 rounded-full border text-sm whitespace-nowrap transition-colors ${selectedStore === name ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider text-text hover:border-text/45'}`}
              >
                {name}
              </button>
            ))}
          </div>
        )}

        <div className="flex gap-2 overflow-x-auto pb-4 -mx-1 px-1" style={{ scrollbarWidth: 'none' }}>
          {PRESETS.map(p => (
            <button
              key={p.value}
              onClick={() => handlePreset(p.value)}
              className={`shrink-0 px-3.5 h-9 rounded-full border text-sm whitespace-nowrap transition-colors ${preset === p.value ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider text-text hover:border-text/45'}`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {preset === "custom" && (
          <div className="grid grid-cols-2 gap-3 pb-4">
            <div>
              <label className="text-xs text-neutral-600 mb-1 block">From month</label>
              <input
                type="month"
                value={months.start_month}
                onChange={e => setMonths(prev => ({ ...prev, start_month: e.target.value }))}
                className="w-full h-10 px-3 rounded-md border border-divider bg-bg text-text text-sm focus:outline-none focus:border-accent"
              />
            </div>
            <div>
              <label className="text-xs text-neutral-600 mb-1 block">To month</label>
              <input
                type="month"
                value={months.end_month}
                onChange={e => setMonths(prev => ({ ...prev, end_month: e.target.value }))}
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
        ) : error || !activeData ? null : (
          <>
            {data?.is_data_complete === false && (
              <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
                <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                <p className="text-accent-700 text-xs leading-snug">
                  Reporting data for this period may be incomplete &mdash; run a Tally sync to make sure everything is up to date.
                </p>
              </div>
            )}
            {missingStores.length > 0 && (
              <div className="flex items-start gap-2 border border-accent rounded-md p-3 mb-4">
                <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                <p className="text-accent-700 text-xs leading-snug">
                  Stock data is missing for {missingStores.join(', ')} &mdash; gross/net profit can't be computed until it syncs.
                </p>
              </div>
            )}

            {/* Summary */}
            <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">{storeLabel} · {periodLabel}{preset === 'custom' ? ` · ${dateRangeFull}` : ''}</div>
            <div className="text-sm text-neutral-700 mt-2">Net sales</div>
            <div className="font-heading text-[40px] leading-tight mt-1">{fmtMoney(activeData.revenue.net_sales)}</div>
            {netSalesChange !== null && <div className="text-sm mt-1.5"><ChangeIndicator pct={netSalesChange} /></div>}

            <div className="border-t border-divider my-5" />

            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="text-sm text-neutral-700">Gross profit{grossMargin !== null ? ` · ${grossMargin.toFixed(1)}%` : ''}</div>
                <div className="font-heading font-semibold text-2xl mt-1">{activeData.gross_profit !== null ? fmtMoney(activeData.gross_profit) : '—'}</div>
                {grossProfitChange !== null && <div className="text-xs mt-1"><ChangeIndicator pct={grossProfitChange} /></div>}
              </div>
              <div>
                <div className="text-sm text-neutral-700">Net profit{netMargin !== null ? ` · ${netMargin.toFixed(1)}%` : ''}</div>
                <div className="font-heading font-semibold text-2xl mt-1">{activeData.net_profit !== null ? fmtMoney(activeData.net_profit) : '—'}</div>
                {netProfitChange !== null && <div className="text-xs mt-1"><ChangeIndicator pct={netProfitChange} /></div>}
              </div>
            </div>

            <div className="border-t border-divider my-5" />

            {/* Statement */}
            <h3 className="font-heading font-semibold text-xl">Profit &amp; loss statement</h3>
            <p className="text-sm text-neutral-600 mt-1 mb-4">{storeLabelStatement} · {dateRangeFull}</p>

            <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700 mb-1">Revenue</div>
            <div className="flex justify-between py-2 border-b border-divider">
              <span className="text-[15px]">Net sales</span>
              <span className="text-[15px]">{fmtMoney2(activeData.revenue.net_sales)}</span>
            </div>

            <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700 mt-4 mb-1">Cost of goods sold</div>
            <div className="flex justify-between py-2 border-b border-divider">
              <span className="text-[15px]">Opening stock</span>
              <span className="text-[15px]">{activeData.cost_of_goods_sold.opening_stock !== null ? fmtMoney2(activeData.cost_of_goods_sold.opening_stock) : '—'}</span>
            </div>
            <div className="flex justify-between py-2 border-b border-divider">
              <span className="text-[15px]">Net purchases</span>
              <span className="text-[15px]">{fmtMoney2(activeData.cost_of_goods_sold.net_purchases)}</span>
            </div>
            <div className="flex justify-between py-2 border-b border-divider">
              <span className="text-[15px]">Less: Closing stock</span>
              <span className="text-[15px]">{activeData.cost_of_goods_sold.closing_stock !== null ? `(${fmtMoney2(activeData.cost_of_goods_sold.closing_stock)})` : '—'}</span>
            </div>
            <div className="flex justify-between py-2 font-semibold">
              <span className="text-[15px]">Cost of goods sold</span>
              <span className="text-[15px]">{activeData.cost_of_goods_sold.cogs !== null ? fmtMoney2(activeData.cost_of_goods_sold.cogs) : '—'}</span>
            </div>

            <div className="flex justify-between items-baseline border border-accent rounded-md px-4 py-3 mt-4">
              <span className="font-heading text-lg">Gross profit</span>
              <span className="font-heading font-semibold text-xl">{activeData.gross_profit !== null ? fmtMoney2(activeData.gross_profit) : '—'}</span>
            </div>

            <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700 mt-5 mb-1">Expenses</div>
            <div className="flex justify-between py-2 border-b border-divider font-medium">
              <span className="text-[15px]">Direct expenses</span>
              <span className="text-[15px]">{activeData.expenses.direct_expenses !== 0 ? fmtMoney2(activeData.expenses.direct_expenses) : '—'}</span>
            </div>
            {(activeData.expenses.direct_expenses_items || []).map(item => (
              <div key={item.ledger_name} className="flex justify-between py-1.5 pl-4 text-neutral-700">
                <span className="text-sm">{item.ledger_name}</span>
                <span className="text-sm">{fmtMoney2(item.amount)}</span>
              </div>
            ))}
            <div className="flex justify-between py-2 border-b border-divider font-medium mt-1">
              <span className="text-[15px]">Indirect expenses</span>
              <span className="text-[15px]">{activeData.expenses.indirect_expenses !== 0 ? fmtMoney2(activeData.expenses.indirect_expenses) : '—'}</span>
            </div>
            {(activeData.expenses.indirect_expenses_items || []).map(item => (
              <div key={item.ledger_name} className="flex justify-between py-1.5 pl-4 text-neutral-700">
                <span className="text-sm">{item.ledger_name}</span>
                <span className="text-sm">{fmtMoney2(item.amount)}</span>
              </div>
            ))}
            <div className="flex justify-between py-2 font-semibold mt-1">
              <span className="text-[15px]">Total expenses</span>
              <span className="text-[15px]">{fmtMoney2((activeData.expenses.direct_expenses || 0) + (activeData.expenses.indirect_expenses || 0))}</span>
            </div>

            <div className="flex justify-between items-baseline border border-accent rounded-md px-4 py-3 mt-4">
              <span className="font-heading text-lg">Net profit</span>
              <span className="font-heading font-semibold text-xl text-accent-700">{activeData.net_profit !== null ? fmtMoney2(activeData.net_profit) : '—'}</span>
            </div>

            {/* Store comparison */}
            {storeCards.length > 0 && (
              <>
                <div className="border-t border-divider my-5" />
                <h3 className="font-heading font-semibold text-xl">Store comparison</h3>
                <p className="text-sm text-neutral-600 mt-1 mb-4">Tap a store to see its statement</p>

                <div className="flex flex-col gap-3">
                  {storeCards.map(s => (
                    <button
                      key={s.store}
                      onClick={() => setSelectedStore(s.store)}
                      className="text-left border border-divider rounded-md p-4 hover:border-accent transition-colors"
                    >
                      <div className="flex justify-between items-baseline gap-3">
                        <span className="text-[15px]">{s.store}</span>
                        <div className="text-right shrink-0">
                          <div className="text-xs text-neutral-700">Net profit</div>
                          <div className={`font-heading font-semibold text-lg ${s.net_profit > 0 ? 'text-accent-700' : ''}`}>
                            {s.net_profit !== null ? (s.net_profit < 0 ? '− ' : '') + fmtMoney(Math.abs(s.net_profit)) : '—'}
                          </div>
                        </div>
                      </div>
                      <div className="border-t border-divider my-2.5" />
                      <div className="grid grid-cols-3 gap-2 text-sm">
                        <div>
                          <div className="text-neutral-700 text-xs">Net sales</div>
                          <div>{fmtMoney(s.revenue.net_sales)}</div>
                        </div>
                        <div>
                          <div className="text-neutral-700 text-xs">Purchases</div>
                          <div>{fmtMoney(s.cost_of_goods_sold.net_purchases)}</div>
                        </div>
                        <div>
                          <div className="text-neutral-700 text-xs">Gross profit</div>
                          <div>{s.gross_profit !== null ? fmtMoney(s.gross_profit) : '—'}</div>
                        </div>
                      </div>
                    </button>
                  ))}

                  <div className="text-left border border-accent bg-accent/8 rounded-md p-4">
                    <div className="flex justify-between items-baseline gap-3">
                      <span className="text-[15px] font-semibold">Combined</span>
                      <div className="text-right shrink-0">
                        <div className="text-xs text-neutral-700">Net profit</div>
                        <div className="font-heading font-semibold text-lg text-accent-700">
                          {data.combined.net_profit !== null ? fmtMoney(data.combined.net_profit) : '—'}
                        </div>
                      </div>
                    </div>
                    <div className="border-t border-divider my-2.5" />
                    <div className="grid grid-cols-3 gap-2 text-sm">
                      <div>
                        <div className="text-neutral-700 text-xs">Net sales</div>
                        <div>{fmtMoney(data.combined.revenue.net_sales)}</div>
                      </div>
                      <div>
                        <div className="text-neutral-700 text-xs">Purchases</div>
                        <div>{fmtMoney(data.combined.cost_of_goods_sold.net_purchases)}</div>
                      </div>
                      <div>
                        <div className="text-neutral-700 text-xs">Gross profit</div>
                        <div>{data.combined.gross_profit !== null ? fmtMoney(data.combined.gross_profit) : '—'}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
