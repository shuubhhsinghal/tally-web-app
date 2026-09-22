"use client";

import React, { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { TrendingUp, TrendingDown, ChevronLeft, ArrowUpRight, ArrowDownRight, Store, Calendar, AlertTriangle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const PRESETS = [
    { label: "This Week", value: "week" },
    { label: "This Month", value: "month" },
    { label: "This Quarter", value: "quarter" },
    { label: "This Financial Year", value: "fy" },
    { label: "Custom", value: "custom" },
];

function formatCurrency(val) {
    if (val === undefined || val === null) return "₹0.00";
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        maximumFractionDigits: 0
    }).format(val);
}

function formatShortCurrency(val) {
    if (val === undefined || val === null) return "0";
    if (val >= 100000) return (val / 100000).toFixed(1) + 'L';
    if (val >= 1000) return (val / 1000).toFixed(0) + 'K';
    return val.toString();
}

function getPresetDates(preset) {
    const today = new Date();
    const y = today.getFullYear();
    const m = today.getMonth();
    
    let start, end;
    
    if (preset === "week") {
        const day = today.getDay();
        const diff = today.getDate() - day + (day === 0 ? -6 : 1);
        start = new Date(today.setDate(diff));
        end = new Date(today.setDate(diff + 6));
    } else if (preset === "month") {
        start = new Date(y, m, 1);
        end = new Date(y, m + 1, 0);
    } else if (preset === "quarter") {
        const qMonth = Math.floor(m / 3) * 3;
        start = new Date(y, qMonth, 1);
        end = new Date(y, qMonth + 3, 0);
    } else if (preset === "fy") {
        const fyStartYear = m >= 3 ? y : y - 1;
        start = new Date(fyStartYear, 3, 1);
        end = new Date(fyStartYear + 1, 2, 31);
    } else {
        start = new Date(y, m, 1);
        end = new Date(y, m + 1, 0);
    }
    
    const fmt = (dt) => {
        const yyyy = dt.getFullYear();
        const mm = String(dt.getMonth() + 1).padStart(2, '0');
        const dd = String(dt.getDate()).padStart(2, '0');
        return `${yyyy}${mm}${dd}`;
    };
    return { start: fmt(start), end: fmt(end) };
}

const formatDateInput = (yyyymmdd) => {
    if (!yyyymmdd || yyyymmdd.length !== 8) return "";
    return `${yyyymmdd.substring(0, 4)}-${yyyymmdd.substring(4, 6)}-${yyyymmdd.substring(6, 8)}`;
};

const parseDateInput = (yyyy_mm_dd) => {
    if (!yyyy_mm_dd) return "";
    return yyyy_mm_dd.replace(/-/g, '');
};

const formatDateShort = (yyyymmdd) => {
    if (!yyyymmdd || yyyymmdd.length !== 8) return yyyymmdd;
    const date = new Date(
        parseInt(yyyymmdd.substring(0, 4)),
        parseInt(yyyymmdd.substring(4, 6)) - 1,
        parseInt(yyyymmdd.substring(6, 8))
    );
    return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' }).format(date);
};

const formatDateFull = (yyyymmdd) => {
    if (!yyyymmdd || yyyymmdd.length !== 8) return yyyymmdd;
    const date = new Date(
        parseInt(yyyymmdd.substring(0, 4)),
        parseInt(yyyymmdd.substring(4, 6)) - 1,
        parseInt(yyyymmdd.substring(6, 8))
    );
    return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(date);
};

export default function SalesReport() {
    const router = useRouter();
    const { user } = useAuth();
    const lockedStore = user && !user.is_owner ? user.store_name : null;
    const [isMounted, setIsMounted] = useState(false);
    useEffect(() => setIsMounted(true), []);

    const [loading, setLoading] = useState(false);
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [preset, setPreset] = useState("month");
    const [dates, setDates] = useState(getPresetDates("month"));
    const [costCentres, setCostCentres] = useState([]);
    const [selectedStore, setSelectedStore] = useState(lockedStore || "");

    useEffect(() => {
        if (lockedStore) setSelectedStore(lockedStore);
    }, [lockedStore]);

    useEffect(() => {
        fetch(`${API_BASE}/api/reporting/inspect/cost-centres`)
            .then(res => res.json())
            .then(d => {
                if(Array.isArray(d)) setCostCentres(d);
            })
            .catch(err => console.error(err));
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
            .then(d => { setData(d); setError(null); })
            .catch(err => setError(err.message))
            .finally(() => setLoading(false));
    }, [dates, selectedStore]);

    const handlePresetChange = (p) => {
        setPreset(p);
        if (p !== "custom") {
            setDates(getPresetDates(p));
        }
    };

    if (!isMounted) return null;

    // Derived Data Calculations
    const trendData = data?.trend || [];
    let highestDay = null;
    let lowestDay = null;
    let storeColors = ['bg-blue-500', 'bg-cyan-400', 'bg-purple-400', 'bg-indigo-500', 'bg-teal-400'];

    if (trendData.length > 0) {
        const sorted = [...trendData].sort((a, b) => b.Combined - a.Combined);
        highestDay = sorted[0];
        lowestDay = sorted.filter(d => d.Combined > 0).pop() || sorted[sorted.length - 1]; // Lowest non-zero day
    }

    const storeComparison = data?.store_comparison?.filter(s => s.net_sales > 0).sort((a, b) => b.net_sales - a.net_sales) || [];
    const totalSalesFromStores = storeComparison.reduce((sum, s) => sum + s.net_sales, 0);

    // Compute Day-by-Day Table Data
    const dayByDay = [...trendData].reverse().filter(d => d.Combined > 0).map(d => {
        // Find top store for the day
        let topStore = "Unknown";
        let maxStoreAmt = -1;
        Object.keys(d).forEach(k => {
            if (k !== 'date' && k !== 'Combined' && k !== 'Combined_invoices') {
                if (d[k] > maxStoreAmt) {
                    maxStoreAmt = d[k];
                    topStore = k;
                }
            }
        });

        return {
            date: d.date,
            invoices: d.Combined_invoices || 0,
            topStore,
            amount: d.Combined
        };
    });

    const CustomTooltip = ({ active, payload, label }) => {
        if (active && payload && payload.length) {
            return (
                <div className="bg-slate-800 border border-slate-700 p-3 rounded-xl shadow-xl">
                    <p className="text-slate-400 text-xs font-medium mb-1">{formatDateShort(label)}</p>
                    <p className="text-white font-bold text-lg">{formatCurrency(payload[0].value)}</p>
                </div>
            );
        }
        return null;
    };

    return (
        <div className="min-h-screen bg-[#0f172a] text-slate-200 pb-24 font-sans selection:bg-cyan-500/30">
            {/* Header */}
            <div className="px-5 pt-12 pb-4">
                <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                        <button onClick={() => router.back()} className="p-1 hover:bg-slate-800 rounded-full transition-colors">
                            <ChevronLeft className="w-7 h-7 text-white" />
                        </button>
                        <div>
                            <h1 className="text-2xl font-bold text-white tracking-tight whitespace-nowrap">Sales Report</h1>
                            <p className="text-slate-400 text-xs sm:text-sm">Analyze your sales performance</p>
                        </div>
                    </div>
                </div>

                {/* Filters */}
                <div className="flex gap-4 mt-6">
                    <div className="flex-1">
                        <label className="text-xs font-medium text-slate-500 mb-1.5 block">Store</label>
                        <div className="relative">
                            {lockedStore ? (
                                <div className="w-full bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 pl-3 pr-8 rounded-xl truncate">
                                    {lockedStore}
                                </div>
                            ) : (
                            <select
                                value={selectedStore}
                                onChange={e => setSelectedStore(e.target.value)}
                                className="w-full appearance-none bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 pl-3 pr-8 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-500/50 truncate"
                            >
                                <option value="">Combined (All Stores)</option>
                                {costCentres.map(c => <option key={c.id} value={c.name}>{c.name}</option>)}
                            </select>
                            )}
                            <Store className="w-4 h-4 text-slate-500 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                        </div>
                    </div>
                    <div className="flex-1">
                        <label className="text-xs font-medium text-slate-500 mb-1.5 block">Period</label>
                        <div className="relative">
                            <select 
                                value={preset}
                                onChange={e => handlePresetChange(e.target.value)}
                                className="w-full appearance-none bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 pl-3 pr-8 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-500/50 truncate"
                            >
                                {PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                            </select>
                            <Calendar className="w-4 h-4 text-slate-500 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                        </div>
                    </div>
                </div>

                {preset === "custom" && (
                    <div className="flex gap-4 mt-4">
                        <div className="flex-1">
                            <label className="text-xs font-medium text-slate-500 mb-1.5 block">Start Date</label>
                            <input 
                                type="date"
                                value={formatDateInput(dates.start)}
                                onChange={e => setDates(prev => ({ ...prev, start: parseDateInput(e.target.value) }))}
                                className="w-full bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 px-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-500/50"
                            />
                        </div>
                        <div className="flex-1">
                            <label className="text-xs font-medium text-slate-500 mb-1.5 block">End Date</label>
                            <input 
                                type="date"
                                value={formatDateInput(dates.end)}
                                onChange={e => setDates(prev => ({ ...prev, end: parseDateInput(e.target.value) }))}
                                className="w-full bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 px-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-500/50"
                            />
                        </div>
                    </div>
                )}
            </div>

            {error && (
                <div className="mx-5 mb-4 flex items-start gap-2 bg-rose-950/40 border border-rose-800/50 rounded-xl p-3">
                    <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                    <p className="text-rose-300 text-xs leading-snug">{error}</p>
                </div>
            )}

            {loading ? (
                <div className="flex justify-center items-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-400"></div>
                </div>
            ) : error ? null : (
                <div className="px-5 space-y-5">

                    {/* Data-completeness warning -- the periodic Tally sync itself hasn't
                        fully covered this date range yet, so the figures below may be
                        missing vouchers Tally already has (distinct from unsynced_sales
                        below, which is about entries still stuck in THIS app's own queue). */}
                    {data?.is_data_complete === false && (
                        <div className="flex items-start gap-2 bg-amber-950/40 border border-amber-800/50 rounded-xl p-3">
                            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                            <p className="text-amber-300 text-xs leading-snug">
                                Reporting data for this period may be incomplete -- run a Tally sync to make sure everything is up to date.
                            </p>
                        </div>
                    )}

                    {/* Unsynced sales warning. Plain pending entries are now folded into
                        the figures below (see the "confirmed + pending" breakdown under
                        Total Sales), so this only calls out the two cases that are NOT
                        reflected anywhere above: delivery-uncertain entries (safety
                        excluded -- see get_pending_sales_trend's docstring) and entries
                        Tally has actively rejected. */}
                    {(data?.unsynced_sales?.pending_amount || 0) - (data?.summary?.pending_amount || 0) > 0.005 && (
                        <div className="flex items-start gap-2 bg-amber-950/40 border border-amber-800/50 rounded-xl p-3">
                            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                            <p className="text-amber-300 text-xs leading-snug">
                                {formatCurrency(data.unsynced_sales.pending_amount - data.summary.pending_amount)} in sales has an uncertain Tally delivery status and is not included above -- verify manually in the{' '}
                                <button onClick={() => router.push('/queue')} className="underline font-semibold hover:text-amber-200">Queue</button> before it can be counted.
                            </p>
                        </div>
                    )}

                    {data?.unsynced_sales?.failed_count > 0 && (
                        <div className="flex items-start gap-2 bg-rose-950/40 border border-rose-800/50 rounded-xl p-3">
                            <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                            <p className="text-rose-300 text-xs leading-snug">
                                {data.unsynced_sales.failed_count} sales entr{data.unsynced_sales.failed_count === 1 ? 'y' : 'ies'} totaling {formatCurrency(data.unsynced_sales.failed_amount)} failed to sync to Tally and need{data.unsynced_sales.failed_count === 1 ? 's' : ''} attention in the{' '}
                                <button onClick={() => router.push('/queue')} className="underline font-semibold hover:text-rose-200">Queue</button>.
                            </p>
                        </div>
                    )}

                    {/* Total Sales Card */}
                    <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 relative overflow-hidden backdrop-blur-sm">
                        {/* Faint background decorative chart */}
                        <div className="absolute -right-6 -bottom-6 opacity-[0.07] pointer-events-none">
                            <svg width="200" height="150" viewBox="0 0 200 150">
                                <path d="M0 150 L30 100 L70 120 L130 40 L170 60 L200 0" fill="none" stroke="white" strokeWidth="20" strokeLinecap="round" strokeLinejoin="round"/>
                            </svg>
                        </div>
                        
                        <div className="flex items-start gap-4 relative z-10">
                            <div className="w-12 h-12 bg-cyan-950/50 rounded-xl flex items-center justify-center shrink-0 border border-cyan-900/50">
                                <TrendingUp className="w-6 h-6 text-cyan-400" />
                            </div>
                            <div>
                                <p className="text-slate-400 text-sm font-medium mb-1">Total Sales</p>
                                <h2 className="text-4xl font-bold text-white tracking-tight mb-1">
                                    {formatCurrency(data?.summary?.net_sales)}
                                </h2>

                                {data?.summary?.pending_amount > 0 && (
                                    <p className="text-amber-400/90 text-xs mb-2">
                                        {formatCurrency(data.summary.net_sales_confirmed)} confirmed + {formatCurrency(data.summary.pending_amount)} pending Tally confirmation
                                    </p>
                                )}

                                {data?.summary?.change_percentage !== null && data?.summary?.change_percentage !== undefined && (
                                    <div className="flex items-center gap-2">
                                        <div className={`flex items-center gap-1 text-sm font-semibold ${data.summary.change_percentage >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                                            {data.summary.change_percentage >= 0 ? <ArrowUpRight className="w-4 h-4" /> : <ArrowDownRight className="w-4 h-4" />}
                                            {Math.abs(data.summary.change_percentage).toFixed(1)}%
                                        </div>
                                        <p className="text-slate-500 text-xs">
                                            vs. last period ({formatCurrency(data?.summary?.previous_net_sales)})
                                        </p>
                                    </div>
                                )}
                                {(data?.summary?.change_percentage === null || data?.summary?.change_percentage === undefined) && data?.previous_period?.is_data_complete === false && (
                                    <p className="text-slate-500 text-xs italic">
                                        vs. last period unavailable -- that period's Tally sync is incomplete
                                    </p>
                                )}
                            </div>
                        </div>
                    </div>

                    {/* Sales Trend Chart */}
                    <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 backdrop-blur-sm">
                        <div className="flex justify-between items-center mb-6">
                            <div className="flex items-center gap-2">
                                <div className="w-8 h-8 bg-blue-900/30 rounded-lg flex items-center justify-center">
                                    <TrendingUp className="w-4 h-4 text-blue-400" />
                                </div>
                                <h3 className="text-base font-bold text-white">Sales Trend</h3>
                            </div>
                        </div>
                        
                        <div className="h-48 w-full -ml-4">
                            <ResponsiveContainer width="100%" height="100%">
                                <AreaChart data={trendData}>
                                    <defs>
                                        <linearGradient id="colorSales" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.3}/>
                                            <stop offset="95%" stopColor="#06b6d4" stopOpacity={0}/>
                                        </linearGradient>
                                    </defs>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" opacity={0.5} />
                                    <XAxis 
                                        dataKey="date" 
                                        tickFormatter={formatDateShort} 
                                        axisLine={false} 
                                        tickLine={false} 
                                        tick={{fill: '#64748b', fontSize: 10}}
                                        minTickGap={20}
                                        dy={10}
                                    />
                                    <YAxis 
                                        tickFormatter={formatShortCurrency}
                                        axisLine={false}
                                        tickLine={false}
                                        tick={{fill: '#64748b', fontSize: 10}}
                                        dx={-10}
                                    />
                                    <Tooltip content={<CustomTooltip />} />
                                    <Area 
                                        type="monotone" 
                                        dataKey="Combined" 
                                        stroke="#06b6d4" 
                                        strokeWidth={3}
                                        fillOpacity={1} 
                                        fill="url(#colorSales)" 
                                        activeDot={{ r: 6, fill: '#fff', stroke: '#06b6d4', strokeWidth: 3 }}
                                    />
                                </AreaChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    {/* Store-wise Sales */}
                    {storeComparison.length > 0 && !selectedStore && (
                        <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 backdrop-blur-sm">
                            <div className="flex justify-between items-center mb-6">
                                <div className="flex items-center gap-2">
                                    <div className="w-8 h-8 bg-indigo-900/30 rounded-lg flex items-center justify-center">
                                        <Store className="w-4 h-4 text-indigo-400" />
                                    </div>
                                    <h3 className="text-base font-bold text-white">Store-wise Sales</h3>
                                </div>
                            </div>
                            
                            <div className="space-y-5">
                                {storeComparison.map((store, idx) => {
                                    const percentage = totalSalesFromStores > 0 ? (store.net_sales / totalSalesFromStores) * 100 : 0;
                                    const color = storeColors[idx % storeColors.length];
                                    
                                    return (
                                        <div key={store.store_name} className="flex flex-col gap-2">
                                            <div className="flex justify-between items-end">
                                                <span className="text-sm font-medium text-slate-300">{store.store_name}</span>
                                                <div className="flex items-center gap-3">
                                                    <span className="text-sm font-bold text-white">{formatCurrency(store.net_sales)}</span>
                                                    <span className="text-xs text-slate-500 w-8 text-right">{percentage.toFixed(0)}%</span>
                                                </div>
                                            </div>
                                            <div className="h-2 w-full bg-slate-700/50 rounded-full overflow-hidden">
                                                <div className={`h-full ${color} rounded-full`} style={{ width: `${percentage}%` }}></div>
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
                        </div>
                    )}

                    {/* Highest / Lowest Cards */}
                    <div className="grid grid-cols-2 gap-4">
                        <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-4 backdrop-blur-sm flex flex-col items-start gap-2">
                            <div className="w-8 h-8 bg-emerald-900/30 rounded-lg flex items-center justify-center shrink-0">
                                <TrendingUp className="w-4 h-4 text-emerald-400" />
                            </div>
                            <div>
                                <p className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-0.5">Highest Day</p>
                                <p className="text-[11px] font-semibold text-slate-300 mb-0.5">{highestDay ? formatDateFull(highestDay.date) : '-'}</p>
                                <p className="text-sm font-bold text-white">{formatCurrency(highestDay?.Combined)}</p>
                            </div>
                        </div>
                        <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-4 backdrop-blur-sm flex flex-col items-start gap-2">
                            <div className="w-8 h-8 bg-rose-900/30 rounded-lg flex items-center justify-center shrink-0">
                                <TrendingDown className="w-4 h-4 text-rose-400" />
                            </div>
                            <div>
                                <p className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-0.5">Lowest Day</p>
                                <p className="text-[11px] font-semibold text-slate-300 mb-0.5">{lowestDay ? formatDateFull(lowestDay.date) : '-'}</p>
                                <p className="text-sm font-bold text-white">{formatCurrency(lowestDay?.Combined)}</p>
                            </div>
                        </div>
                    </div>

                    {/* Day-by-Day Sales List */}
                    <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 backdrop-blur-sm">
                        <div className="flex justify-between items-center mb-5">
                            <div className="flex items-center gap-2">
                                <div className="w-8 h-8 bg-slate-700/50 rounded-lg flex items-center justify-center">
                                    <Calendar className="w-4 h-4 text-slate-300" />
                                </div>
                                <h3 className="text-base font-bold text-white">Day-by-Day Sales</h3>
                            </div>
                        </div>
                        {data?.summary?.pending_amount > 0 && (
                            <p className="text-slate-500 text-[11px] italic mb-3 -mt-2">Includes entries still pending Tally confirmation.</p>
                        )}

                        <div className="overflow-x-auto">
                            <table className="w-full text-sm text-left">
                                <thead className="text-[10px] text-slate-500 uppercase tracking-wider border-b border-slate-700/50">
                                    <tr>
                                        <th className="pb-3 font-medium">Date</th>
                                        <th className="pb-3 font-medium text-center">Invoices</th>
                                        <th className="pb-3 font-medium hidden sm:table-cell">Top Store</th>
                                        <th className="pb-3 font-medium text-right">Amount</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {dayByDay.length > 0 ? dayByDay.map((day, idx) => (
                                        <tr key={idx} className="border-b border-slate-700/50 last:border-0 hover:bg-slate-700/20 transition-colors">
                                            <td className="py-4 text-slate-300 whitespace-nowrap">
                                                {formatDateFull(day.date)}
                                            </td>
                                            <td className="py-4 text-slate-400 text-center">
                                                {day.invoices}
                                            </td>
                                            <td className="py-4 text-slate-400 hidden sm:table-cell">
                                                {day.topStore}
                                            </td>
                                            <td className="py-4 font-semibold text-white text-right">
                                                {formatCurrency(day.amount)}
                                            </td>
                                        </tr>
                                    )) : (
                                        <tr>
                                            <td colSpan="4" className="py-8 text-center text-slate-500">
                                                No sales recorded for this period.
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
