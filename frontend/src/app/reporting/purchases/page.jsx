"use client";

import React, { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { TrendingUp, TrendingDown, ChevronLeft, ArrowUpRight, ArrowDownRight, Store, Calendar, ShoppingCart, Users, FileText, Package, X, AlertTriangle } from 'lucide-react';
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

export default function PurchasesReport() {
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

    // Supplier Drilldown State
    const [suppliersLoading, setSuppliersLoading] = useState(false);
    const [supplierData, setSupplierData] = useState([]);
    const [selectedSupplier, setSelectedSupplier] = useState(null);
    
    // Day Drilldown State
    const [selectedDay, setSelectedDay] = useState(null);
    const [dayBillsLoading, setDayBillsLoading] = useState(false);
    const [dayBillsData, setDayBillsData] = useState([]);
    
    const [billsLoading, setBillsLoading] = useState(false);
    const [billsData, setBillsData] = useState([]);
    
    const [billDetailsLoading, setBillDetailsLoading] = useState(false);
    const [billDetails, setBillDetails] = useState(null);
    const [listMode, setListMode] = useState("daily"); // daily, suppliers

    useEffect(() => {
        fetch(`${API_BASE}/api/reporting/inspect/cost-centres`)
            .then(res => res.json())
            .then(d => {
                if(Array.isArray(d)) setCostCentres(d);
            })
            .catch(err => console.error(err));
    }, []);

    // Fetch Overview Data
    useEffect(() => {
        if (!dates.start || !dates.end) return;
        setLoading(true);
        let url = `${API_BASE}/api/reporting/purchases?start_date=${dates.start}&end_date=${dates.end}`;
        if (selectedStore) url += `&cost_centre=${encodeURIComponent(selectedStore)}`;
        
        fetch(url)
            .then(res => {
                if (!res.ok) throw new Error("Failed to fetch purchases data");
                return res.json();
            })
            .then(d => { setData(d); setError(null); })
            .catch(err => setError(err.message))
            .finally(() => setLoading(false));
    }, [dates, selectedStore]);

    // Fetch Suppliers Data
    useEffect(() => {
        if (!dates.start || !dates.end) return;
        
        // Reset sub-drilldowns when filters change or mode changes
        setSelectedSupplier(null);
        setSelectedDay(null);
        setBillDetails(null);
        
        setSuppliersLoading(true);
        let url = `${API_BASE}/api/reporting/purchases/suppliers?start_date=${dates.start}&end_date=${dates.end}&limit=100`;
        if (selectedStore) url += `&cost_centre=${encodeURIComponent(selectedStore)}`;
        
        fetch(url)
            .then(res => res.json())
            .then(d => setSupplierData(d.suppliers || []))
            .catch(err => console.error(err))
            .finally(() => setSuppliersLoading(false));
    }, [dates, selectedStore]);

    // Fetch Bills for Supplier
    useEffect(() => {
        if (!selectedSupplier) {
            setBillsData([]);
            return;
        }
        
        setBillsLoading(true);
        let url = `${API_BASE}/api/reporting/purchases/bills?start_date=${dates.start}&end_date=${dates.end}&supplier_name=${encodeURIComponent(selectedSupplier)}&limit=100`;
        if (selectedStore) url += `&cost_centre=${encodeURIComponent(selectedStore)}`;
        
        fetch(url)
            .then(res => res.json())
            .then(d => setBillsData(d.bills || []))
            .catch(err => console.error(err))
            .finally(() => setBillsLoading(false));
    }, [selectedSupplier, dates, selectedStore]);

    // Fetch Bills for Day
    useEffect(() => {
        if (!selectedDay) {
            setDayBillsData([]);
            return;
        }
        setDayBillsLoading(true);
        let url = `${API_BASE}/api/reporting/purchases/bills?start_date=${selectedDay}&end_date=${selectedDay}&limit=100`;
        if (selectedStore) url += `&cost_centre=${encodeURIComponent(selectedStore)}`;
        
        fetch(url)
            .then(res => res.json())
            .then(d => setDayBillsData(d.bills || []))
            .catch(err => console.error(err))
            .finally(() => setDayBillsLoading(false));
    }, [selectedDay, selectedStore]);

    const handleFetchBillDetails = (voucherId) => {
        setBillDetailsLoading(true);
        fetch(`${API_BASE}/api/reporting/purchases/bills/${voucherId}`)
            .then(res => res.json())
            .then(d => setBillDetails(d))
            .catch(err => console.error(err))
            .finally(() => setBillDetailsLoading(false));
    };

    const handlePresetChange = (p) => {
        setPreset(p);
        if (p !== "custom") {
            setDates(getPresetDates(p));
        }
    };

    if (!isMounted) return <div className="min-h-screen bg-[#0f172a]"></div>;

    // Derived Overview Data Calculations
    const trendData = data?.trend || [];
    let highestDay = null;
    let lowestDay = null;
    let storeColors = ['bg-amber-500', 'bg-orange-400', 'bg-pink-400', 'bg-rose-500', 'bg-fuchsia-400'];

    if (trendData.length > 0) {
        const sorted = [...trendData].sort((a, b) => b.Combined - a.Combined);
        highestDay = sorted[0];
        lowestDay = sorted.filter(d => d.Combined > 0).pop() || sorted[sorted.length - 1]; // Lowest non-zero day
    }

    const storeComparison = data?.store_comparison?.filter(s => s.net_purchases > 0).sort((a, b) => b.net_purchases - a.net_purchases) || [];
    const totalPurchasesFromStores = storeComparison.reduce((sum, s) => sum + s.net_purchases, 0);

    const dayByDay = [...trendData].reverse().filter(d => d.Combined > 0).map(d => {
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

    return (
        <div className="min-h-screen bg-[#0f172a] text-slate-200 pb-24 font-sans selection:bg-amber-500/30">
            {/* Header */}
            <div className="px-5 pt-12 pb-4">
                <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                        <button onClick={() => router.back()} className="p-1 hover:bg-slate-800 rounded-full transition-colors">
                            <ChevronLeft className="w-7 h-7 text-white" />
                        </button>
                        <div>
                            <h1 className="text-2xl font-bold text-white tracking-tight whitespace-nowrap">Purchase Report</h1>
                            <p className="text-slate-400 text-xs sm:text-sm">Analyze your purchase activity</p>
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
                                className="w-full appearance-none bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 pl-3 pr-8 rounded-xl focus:outline-none focus:ring-2 focus:ring-amber-500/50 truncate"
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
                                className="w-full appearance-none bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 pl-3 pr-8 rounded-xl focus:outline-none focus:ring-2 focus:ring-amber-500/50 truncate"
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
                                className="w-full bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 px-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-amber-500/50"
                            />
                        </div>
                        <div className="flex-1">
                            <label className="text-xs font-medium text-slate-500 mb-1.5 block">End Date</label>
                            <input 
                                type="date"
                                value={formatDateInput(dates.end)}
                                onChange={e => setDates(prev => ({ ...prev, end: parseDateInput(e.target.value) }))}
                                className="w-full bg-[#1e293b] border border-slate-700/50 text-slate-200 text-sm py-3 px-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-amber-500/50"
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
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-amber-400"></div>
                </div>
            ) : error ? null : (
                <div className="px-5 space-y-5 animate-in fade-in duration-300">

                        {/* Data-completeness warning -- the periodic Tally sync itself hasn't
                            fully covered this date range yet, so the figures below may be
                            missing vouchers Tally already has. */}
                        {data?.is_data_complete === false && (
                            <div className="flex items-start gap-2 bg-amber-950/40 border border-amber-800/50 rounded-xl p-3">
                                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                                <p className="text-amber-300 text-xs leading-snug">
                                    Reporting data for this period may be incomplete -- run a Tally sync to make sure everything is up to date.
                                </p>
                            </div>
                        )}

                        {/* Unsynced purchases warning. Plain pending entries (including
                            pending returns, which are netted in as a deduction) are folded
                            into the figures below -- see the "confirmed + pending" breakdown
                            under Total Purchases -- so this only calls out entries that are
                            NOT reflected anywhere above: delivery-uncertain entries (safety
                            excluded) and entries Tally has actively rejected. */}
                        {(data?.unsynced_purchases?.pending_amount || 0) - (data?.summary?.pending_amount || 0) > 0.005 && (
                            <div className="flex items-start gap-2 bg-amber-950/40 border border-amber-800/50 rounded-xl p-3">
                                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                                <p className="text-amber-300 text-xs leading-snug">
                                    {formatCurrency(data.unsynced_purchases.pending_amount - data.summary.pending_amount)} in purchases has an uncertain Tally delivery status and is not included above -- verify manually in the{' '}
                                    <button onClick={() => router.push('/queue')} className="underline font-semibold hover:text-amber-200">Queue</button> before it can be counted.
                                </p>
                            </div>
                        )}

                        {data?.unsynced_purchases?.failed_count > 0 && (
                            <div className="flex items-start gap-2 bg-rose-950/40 border border-rose-800/50 rounded-xl p-3">
                                <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                                <p className="text-rose-300 text-xs leading-snug">
                                    {data.unsynced_purchases.failed_count} purchase entr{data.unsynced_purchases.failed_count === 1 ? 'y' : 'ies'} totaling {formatCurrency(data.unsynced_purchases.failed_amount)} failed to sync to Tally and need{data.unsynced_purchases.failed_count === 1 ? 's' : ''} attention in the{' '}
                                    <button onClick={() => router.push('/queue')} className="underline font-semibold hover:text-rose-200">Queue</button>.
                                </p>
                            </div>
                        )}

                        {/* Total Purchases Card */}
                        <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 relative overflow-hidden backdrop-blur-sm">
                            <div className="absolute -right-6 -bottom-6 opacity-[0.07] pointer-events-none">
                                <svg width="200" height="150" viewBox="0 0 200 150">
                                    <path d="M0 150 L30 100 L70 120 L130 40 L170 60 L200 0" fill="none" stroke="white" strokeWidth="20" strokeLinecap="round" strokeLinejoin="round"/>
                                </svg>
                            </div>
                            
                            <div className="flex items-start gap-4 relative z-10">
                                <div className="w-12 h-12 bg-amber-950/50 rounded-xl flex items-center justify-center shrink-0 border border-amber-900/50">
                                    <ShoppingCart className="w-6 h-6 text-amber-400" />
                                </div>
                                <div>
                                    <p className="text-slate-400 text-sm font-medium mb-1">Total Purchases</p>
                                    <h2 className="text-4xl font-bold text-white tracking-tight mb-1">
                                        {formatCurrency(data?.summary?.net_purchases)}
                                    </h2>

                                    {Math.abs(data?.summary?.pending_amount || 0) > 0.005 && (
                                        <p className="text-amber-400/90 text-xs mb-2">
                                            {formatCurrency(data.summary.net_purchases_confirmed)} confirmed {data.summary.pending_amount >= 0 ? '+' : '-'} {formatCurrency(Math.abs(data.summary.pending_amount))} pending Tally confirmation
                                        </p>
                                    )}

                                    {data?.summary?.change_pct !== null && data?.summary?.change_pct !== undefined && (
                                        <div className="flex items-center gap-2">
                                            <div className={`flex items-center gap-1 text-sm font-semibold ${data.summary.change_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                                                {data.summary.change_pct >= 0 ? <ArrowUpRight className="w-4 h-4" /> : <ArrowDownRight className="w-4 h-4" />}
                                                {Math.abs(data.summary.change_pct).toFixed(1)}%
                                            </div>
                                            <p className="text-slate-500 text-xs">
                                                vs. last period ({formatCurrency(data?.summary?.previous_purchases)})
                                            </p>
                                        </div>
                                    )}
                                    {(data?.summary?.change_pct === null || data?.summary?.change_pct === undefined) && data?.previous_period?.is_data_complete === false && (
                                        <p className="text-slate-500 text-xs italic">
                                            vs. last period unavailable -- that period's Tally sync is incomplete
                                        </p>
                                    )}
                                </div>
                            </div>
                        </div>

                        {/* Purchase Trend Chart */}
                        <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 backdrop-blur-sm">
                            <div className="flex justify-between items-center mb-6">
                                <div className="flex items-center gap-2">
                                    <div className="w-8 h-8 bg-amber-900/30 rounded-lg flex items-center justify-center">
                                        <TrendingUp className="w-4 h-4 text-amber-400" />
                                    </div>
                                    <h3 className="text-base font-bold text-white">Purchase Trend</h3>
                                </div>
                            </div>
                            
                            <div className="h-48 w-full -ml-4">
                                <ResponsiveContainer width="100%" height="100%">
                                    <AreaChart data={trendData}>
                                        <defs>
                                            <linearGradient id="colorPurchases" x1="0" y1="0" x2="0" y2="1">
                                                <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3}/>
                                                <stop offset="95%" stopColor="#f59e0b" stopOpacity={0}/>
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
                                            stroke="#f59e0b" 
                                            strokeWidth={3}
                                            fillOpacity={1} 
                                            fill="url(#colorPurchases)" 
                                            activeDot={{ r: 6, fill: '#fff', stroke: '#f59e0b', strokeWidth: 3 }}
                                        />
                                    </AreaChart>
                                </ResponsiveContainer>
                            </div>
                        </div>

                        {/* Store-wise Purchases */}
                        {storeComparison.length > 0 && !selectedStore && (
                            <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 backdrop-blur-sm">
                                <div className="flex justify-between items-center mb-6">
                                    <div className="flex items-center gap-2">
                                        <div className="w-8 h-8 bg-pink-900/30 rounded-lg flex items-center justify-center">
                                            <Store className="w-4 h-4 text-pink-400" />
                                        </div>
                                        <h3 className="text-base font-bold text-white">Store-wise Purchases</h3>
                                    </div>
                                </div>
                                
                                <div className="space-y-5">
                                    {storeComparison.map((store, idx) => {
                                        const percentage = totalPurchasesFromStores > 0 ? (store.net_purchases / totalPurchasesFromStores) * 100 : 0;
                                        const color = storeColors[idx % storeColors.length];
                                        
                                        return (
                                            <div key={store.store_name} className="flex flex-col gap-2">
                                                <div className="flex justify-between items-end">
                                                    <span className="text-sm font-medium text-slate-300">{store.store_name}</span>
                                                    <div className="flex items-center gap-3">
                                                        <span className="text-sm font-bold text-white">{formatCurrency(store.net_purchases)}</span>
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

                        {/* Lists Section */}
                        {!selectedSupplier && !selectedDay ? (
                            <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 backdrop-blur-sm">
                                {/* Toggle Header */}
                                <div className="flex gap-2 p-1 bg-slate-900/50 rounded-xl mb-5">
                                    <button 
                                        onClick={() => setListMode("daily")}
                                        className={`flex-1 py-2 text-xs sm:text-sm font-medium rounded-lg transition-colors flex items-center justify-center gap-2 ${listMode === "daily" ? "bg-amber-500/20 text-amber-400" : "text-slate-400 hover:text-slate-200"}`}
                                    >
                                        <Calendar className="w-4 h-4" />
                                        Day-by-Day
                                    </button>
                                    <button 
                                        onClick={() => setListMode("suppliers")}
                                        className={`flex-1 py-2 text-xs sm:text-sm font-medium rounded-lg transition-colors flex items-center justify-center gap-2 ${listMode === "suppliers" ? "bg-amber-500/20 text-amber-400" : "text-slate-400 hover:text-slate-200"}`}
                                    >
                                        <Users className="w-4 h-4" />
                                        Top Suppliers
                                    </button>
                                </div>

                                {listMode === "daily" ? (
                                    <div className="overflow-x-auto">
                                        {Math.abs(data?.summary?.pending_amount || 0) > 0.005 && (
                                            <p className="text-slate-500 text-[11px] italic mb-3">Includes entries still pending Tally confirmation.</p>
                                        )}
                                        <table className="w-full text-sm text-left animate-in fade-in duration-300">
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
                                                    <tr 
                                                        key={idx} 
                                                        onClick={() => setSelectedDay(day.date)}
                                                        className="border-b border-slate-700/50 last:border-0 hover:bg-slate-700/40 transition-colors cursor-pointer group"
                                                    >
                                                        <td className="py-4 text-slate-300 whitespace-nowrap group-hover:text-amber-400 transition-colors">
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
                                                            No purchases recorded for this period.
                                                        </td>
                                                    </tr>
                                                )}
                                            </tbody>
                                        </table>
                                    </div>
                                ) : (
                                    suppliersLoading ? (
                                        <div className="flex justify-center items-center h-32">
                                            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-amber-400"></div>
                                        </div>
                                    ) : (
                                        <div className="overflow-x-auto">
                                            {Math.abs(data?.summary?.pending_amount || 0) > 0.005 && (
                                                <p className="text-slate-500 text-[11px] italic mb-3">Confirmed purchases only -- doesn't yet include entries still pending Tally confirmation.</p>
                                            )}
                                            <table className="w-full text-sm text-left animate-in fade-in duration-300">
                                                <thead className="text-[10px] text-slate-500 uppercase tracking-wider border-b border-slate-700/50">
                                                    <tr>
                                                        <th className="pb-3 font-medium">Supplier</th>
                                                        <th className="pb-3 font-medium text-center">Bills</th>
                                                        <th className="pb-3 font-medium text-right">Total Amount</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {supplierData.length > 0 ? supplierData.map((supp, idx) => (
                                                        <tr 
                                                            key={idx} 
                                                            onClick={() => setSelectedSupplier(supp.supplier_name)}
                                                            className="border-b border-slate-700/50 last:border-0 hover:bg-slate-700/40 transition-colors cursor-pointer group"
                                                        >
                                                            <td className="py-4 text-amber-100 font-medium group-hover:text-amber-400 transition-colors">
                                                                {supp.supplier_name}
                                                            </td>
                                                            <td className="py-4 text-slate-400 text-center">
                                                                {supp.vouchers_count}
                                                            </td>
                                                            <td className="py-4 font-semibold text-white text-right">
                                                                {formatCurrency(supp.net_purchases)}
                                                            </td>
                                                        </tr>
                                                    )) : (
                                                        <tr>
                                                            <td colSpan="3" className="py-8 text-center text-slate-500">
                                                                No suppliers found for this period.
                                                            </td>
                                                        </tr>
                                                    )}
                                                </tbody>
                                            </table>
                                        </div>
                                    )
                                )}
                            </div>
                        ) : selectedSupplier ? (
                        // Supplier Bills Drilldown
                        <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 backdrop-blur-sm animate-in slide-in-from-right-4 duration-300">
                            <div className="flex items-center gap-3 mb-6">
                                <button onClick={() => { setSelectedSupplier(null); setBillDetails(null); }} className="p-1 hover:bg-slate-700 rounded-full transition-colors">
                                    <ChevronLeft className="w-6 h-6 text-slate-400 hover:text-white" />
                                </button>
                                <div>
                                    <h3 className="text-base font-bold text-white">{selectedSupplier}</h3>
                                    <p className="text-xs text-slate-400">Bills for selected period</p>
                                </div>
                            </div>

                            {billsLoading ? (
                                <div className="flex justify-center items-center h-32">
                                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-amber-400"></div>
                                </div>
                            ) : (
                                <div className="overflow-x-auto">
                                    <table className="w-full text-sm text-left">
                                        <thead className="text-[10px] text-slate-500 uppercase tracking-wider border-b border-slate-700/50">
                                            <tr>
                                                <th className="pb-3 font-medium">Date</th>
                                                <th className="pb-3 font-medium">Ref No.</th>
                                                <th className="pb-3 font-medium text-right">Amount</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {billsData.length > 0 ? billsData.map((bill, idx) => (
                                                <tr
                                                    key={idx}
                                                    onClick={() => !bill.is_pending && handleFetchBillDetails(bill.voucher_id)}
                                                    title={bill.is_pending ? "Not yet confirmed by Tally -- no details to view yet" : undefined}
                                                    className={`border-b border-slate-700/50 last:border-0 transition-colors group ${bill.is_pending ? "opacity-70 cursor-default" : "hover:bg-slate-700/40 cursor-pointer"}`}
                                                >
                                                    <td className="py-4 text-slate-300 whitespace-nowrap">
                                                        {formatDateFull(bill.date)}
                                                        {bill.is_pending && (
                                                            <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-400 align-middle">
                                                                Pending
                                                            </span>
                                                        )}
                                                    </td>
                                                    <td className="py-4 text-slate-400 font-mono text-xs">
                                                        {bill.voucher_number || '-'}
                                                    </td>
                                                    <td className="py-4 font-semibold text-white text-right group-hover:text-amber-400 transition-colors">
                                                        {formatCurrency(bill.net_purchases)}
                                                    </td>
                                                </tr>
                                            )) : (
                                                <tr>
                                                    <td colSpan="3" className="py-8 text-center text-slate-500">
                                                        No bills found for this supplier.
                                                    </td>
                                                </tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    ) : (
                        // Day Bills Drilldown
                        <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 backdrop-blur-sm animate-in slide-in-from-right-4 duration-300">
                            <div className="flex items-center gap-3 mb-6">
                                <button onClick={() => { setSelectedDay(null); setBillDetails(null); }} className="p-1 hover:bg-slate-700 rounded-full transition-colors">
                                    <ChevronLeft className="w-6 h-6 text-slate-400 hover:text-white" />
                                </button>
                                <div>
                                    <h3 className="text-base font-bold text-white">{formatDateFull(selectedDay)}</h3>
                                    <p className="text-xs text-slate-400">Purchases on this day</p>
                                </div>
                            </div>

                            {dayBillsLoading ? (
                                <div className="flex justify-center items-center h-32">
                                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-amber-400"></div>
                                </div>
                            ) : (
                                <div className="overflow-x-auto">
                                    <table className="w-full text-sm text-left">
                                        <thead className="text-[10px] text-slate-500 uppercase tracking-wider border-b border-slate-700/50">
                                            <tr>
                                                <th className="pb-3 font-medium">Supplier</th>
                                                <th className="pb-3 font-medium">Ref No.</th>
                                                <th className="pb-3 font-medium text-right">Amount</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {dayBillsData.length > 0 ? dayBillsData.map((bill, idx) => (
                                                <tr
                                                    key={idx}
                                                    onClick={() => !bill.is_pending && handleFetchBillDetails(bill.voucher_id)}
                                                    title={bill.is_pending ? "Not yet confirmed by Tally -- no details to view yet" : undefined}
                                                    className={`border-b border-slate-700/50 last:border-0 transition-colors group ${bill.is_pending ? "opacity-70 cursor-default" : "hover:bg-slate-700/40 cursor-pointer"}`}
                                                >
                                                    <td className="py-4 text-slate-300 whitespace-nowrap">
                                                        {bill.supplier_name}
                                                        {bill.is_pending && (
                                                            <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-400 align-middle">
                                                                Pending
                                                            </span>
                                                        )}
                                                    </td>
                                                    <td className="py-4 text-slate-400 font-mono text-xs">
                                                        {bill.voucher_number || '-'}
                                                    </td>
                                                    <td className="py-4 font-semibold text-white text-right group-hover:text-amber-400 transition-colors">
                                                        {formatCurrency(bill.net_purchases)}
                                                    </td>
                                                </tr>
                                            )) : (
                                                <tr>
                                                    <td colSpan="3" className="py-8 text-center text-slate-500">
                                                        No bills found for this day.
                                                    </td>
                                                </tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* Bill Details Modal Overlay */}
            {billDetails && (
                <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200">
                    <div className="bg-slate-900 border border-slate-700 w-full max-w-lg rounded-t-2xl sm:rounded-2xl shadow-2xl overflow-hidden animate-in slide-in-from-bottom-8 sm:slide-in-from-bottom-4 duration-300">
                        <div className="flex justify-between items-center p-5 border-b border-slate-800">
                            <div>
                                <h4 className="text-lg font-bold text-white">Bill Details</h4>
                                <p className="text-xs text-slate-400">Ref: {billDetails.voucher?.voucher_number || '-'} • {formatDateFull(billDetails.voucher?.date)}</p>
                            </div>
                            <button onClick={() => setBillDetails(null)} className="p-2 hover:bg-slate-800 rounded-full transition-colors text-slate-400 hover:text-white">
                                <X className="w-5 h-5" />
                            </button>
                        </div>
                        <div className="p-5 max-h-[60vh] overflow-y-auto">
                            {billDetailsLoading ? (
                                <div className="flex justify-center items-center h-32">
                                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-amber-400"></div>
                                </div>
                            ) : billDetails.items && billDetails.items.length > 0 ? (
                                <div className="space-y-4">
                                    {billDetails.items.map((item, idx) => {
                                        const calculatedRate = item.rate || (Math.abs(item.billed_qty) > 0 ? Math.abs(item.amount) / Math.abs(item.billed_qty) : 0);
                                        return (
                                            <div key={idx} className="bg-slate-800/50 border border-slate-700/50 p-4 rounded-xl flex gap-4">
                                                <div className="w-10 h-10 bg-slate-700/50 rounded-lg flex items-center justify-center shrink-0">
                                                    <Package className="w-5 h-5 text-slate-400" />
                                                </div>
                                                <div className="flex-1">
                                                    <p className="text-sm font-semibold text-slate-200 mb-1">{item.stock_item_name}</p>
                                                    <div className="flex justify-between items-end">
                                                        <p className="text-xs text-slate-400">
                                                            <span className="font-mono text-amber-400/80">{Math.abs(item.billed_qty)}</span>
                                                            <span className="ml-1 text-[10px] uppercase">QTY</span>
                                                            <span className="mx-2">•</span>
                                                            <span className="font-mono">{formatCurrency(calculatedRate)}</span>
                                                            <span className="ml-1 text-[10px] uppercase">Rate</span>
                                                        </p>
                                                        <p className="text-sm font-bold text-white">{formatCurrency(Math.abs(item.amount))}</p>
                                                    </div>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : (
                                <div className="text-center py-8 text-slate-500 text-sm">
                                    No inventory items found for this bill.
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
