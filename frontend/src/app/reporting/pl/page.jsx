"use client";

import React, { useState, useEffect } from 'react';
import { AlertCircle, TrendingUp, TrendingDown, RefreshCw, Calendar, Store, Info, ChevronLeft, ChevronRight, ArrowLeft } from 'lucide-react';
import ReportTabs from '@/components/layout/ReportTabs';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const PRESETS = [
    { label: "This Month", value: "month" },
    { label: "This Quarter", value: "quarter" },
    { label: "This Financial Year", value: "fy" },
    { label: "Custom Month Range", value: "custom" },
];

function formatCurrency(val) {
    if (val === undefined || val === null) return "—";
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        minimumFractionDigits: 2
    }).format(val);
}

function getPresetMonths(preset) {
    const today = new Date();
    const y = today.getFullYear();
    const m = today.getMonth();
    
    let startMonthStr, endMonthStr;
    
    const fmt = (year, monthIdx) => {
        return `${year}-${String(monthIdx + 1).padStart(2, '0')}`;
    };
    
    if (preset === "month") {
        startMonthStr = fmt(y, m);
        endMonthStr = fmt(y, m);
    } else if (preset === "quarter") {
        const qMonth = Math.floor(m / 3) * 3;
        startMonthStr = fmt(y, qMonth);
        endMonthStr = fmt(y, qMonth + 2);
    } else if (preset === "fy") {
        const fyStartYear = m >= 3 ? y : y - 1;
        startMonthStr = fmt(fyStartYear, 3); // April
        endMonthStr = fmt(fyStartYear + 1, 2); // March
    } else {
        startMonthStr = fmt(y, m);
        endMonthStr = fmt(y, m);
    }
    
    return { start_month: startMonthStr, end_month: endMonthStr };
}

export default function PLReport() {
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    
    const [preset, setPreset] = useState("fy"); // Default FY
    const [months, setMonths] = useState(getPresetMonths("fy"));
    
    const [costCentres, setCostCentres] = useState([]);
    const [selectedStore, setSelectedStore] = useState("Combined");

    useEffect(() => {
        fetchCostCentres();
    }, []);
    
    useEffect(() => {
        if (preset !== "custom") {
            setMonths(getPresetMonths(preset));
        }
    }, [preset]);
    
    useEffect(() => {
        if (months.start_month && months.end_month) {
            fetchReport();
        }
    }, [months]);

    const fetchCostCentres = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/reporting/inspect/cost-centres`);
            if (res.ok) {
                const json = await res.json();
                setCostCentres(json);
            }
        } catch (err) {}
    };

    const fetchReport = async () => {
        try {
            setLoading(true);
            const res = await fetch(`${API_BASE}/api/reporting/profit-loss?start_month=${months.start_month}&end_month=${months.end_month}`);
            const json = await res.json();
            if (!res.ok) {
                throw new Error(json.detail || "Failed to fetch P&L Report");
            }
            
            setData(json);
            setError(null);
        } catch (err) {
            setError(err.message);
            setData(null);
        } finally {
            setLoading(false);
        }
    };

    const handleCustomMonthChange = (type, val) => {
        setPreset("custom");
        setMonths(prev => ({ ...prev, [type]: val }));
    };

    const getActiveData = () => {
        if (!data) return null;
        if (selectedStore === "Combined") return data.combined;
        if (selectedStore === "Unallocated") return data.unallocated;
        return data.stores.find(s => s.store === selectedStore);
    };

    const getActivePreviousData = () => {
        if (!data || !data.previous_period.is_data_complete) return null;
        if (selectedStore === "Combined") return data.combined_previous;
        if (selectedStore === "Unallocated") return data.unallocated_previous;
        return data.stores_previous.find(s => s.store === selectedStore);
    };

    const activeData = getActiveData();
    const activePreviousData = getActivePreviousData();

    const missingStores = activeData?.missing_stores || [];
    const isMissingStock = missingStores.length > 0;

    const renderKPICard = (title, value, prevValue) => {
        const hasValue = value !== null && value !== undefined;
        let changePct = null;
        
        if (hasValue && prevValue !== null && prevValue !== undefined) {
            const diff = value - prevValue;
            changePct = prevValue !== 0 ? (diff / Math.abs(prevValue)) * 100 : (value > 0 ? 100 : 0);
        }

        return (
            <div className="bg-white dark:bg-gray-800 p-6 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm relative overflow-hidden group">
                <p className="text-sm font-semibold text-gray-500 uppercase tracking-wide">{title}</p>
                <h2 className={`text-3xl lg:text-4xl font-black mt-2 tracking-tight ${!hasValue ? 'text-gray-400' : 'text-gray-900 dark:text-white'}`}>
                    {hasValue ? formatCurrency(value) : '—'}
                </h2>
                {changePct !== null && (
                    <div className="mt-4 flex items-center gap-2">
                        <span className={`flex items-center text-sm font-semibold px-2 py-1 rounded-lg ${
                            changePct > 0 ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 
                            changePct < 0 ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' : 
                            'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400'
                        }`}>
                            {changePct > 0 ? <TrendingUp className="w-3 h-3 mr-1" /> : 
                             changePct < 0 ? <TrendingDown className="w-3 h-3 mr-1" /> : 
                             <RefreshCw className="w-3 h-3 mr-1" />}
                            {changePct > 0 ? '+' : ''}{changePct.toFixed(1)}%
                        </span>
                        <span className="text-xs font-medium text-gray-400">vs Prev Period</span>
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="w-full flex flex-col min-h-screen pb-20">
            <ReportTabs />
            <div className="p-6 max-w-7xl mx-auto space-y-6 w-full flex-1">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900 dark:text-white tracking-tight">Profit &amp; Loss</h1>
                        <p className="text-gray-500 dark:text-gray-400 mt-1">Store-wise revenue, expenses, and profitability.</p>
                    </div>
                    
                    <div className="flex flex-wrap items-center gap-3">
                        <div className="relative">
                            <Store className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                            <select 
                                value={selectedStore} 
                                onChange={(e) => setSelectedStore(e.target.value)}
                                className="pl-9 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-xl bg-white dark:bg-gray-800 text-sm focus:ring-2 focus:ring-blue-500 shadow-sm appearance-none cursor-pointer"
                            >
                                <option value="Combined">Combined (All Stores)</option>
                                <option value="Unallocated">Unallocated</option>
                                {costCentres.map(c => (
                                    <option key={c.name} value={c.name}>{c.name}</option>
                                ))}
                            </select>
                        </div>
                        
                        <div className="flex bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-xl p-1 shadow-sm overflow-x-auto max-w-[calc(100vw-3rem)]">
                            {PRESETS.map(p => (
                                <button
                                    key={p.value}
                                    onClick={() => setPreset(p.value)}
                                    className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors whitespace-nowrap ${preset === p.value ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
                                >
                                    {p.label}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
                
                {preset === "custom" && (
                    <div className="flex items-center gap-4 bg-gray-50 dark:bg-gray-800/50 p-4 rounded-xl border border-gray-200 dark:border-gray-800 flex-wrap">
                        <div className="flex flex-col">
                            <label className="text-xs text-gray-500 font-medium mb-1">Start Month</label>
                            <input type="month" value={months.start_month} onChange={(e) => handleCustomMonthChange("start_month", e.target.value)} className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-sm" />
                        </div>
                        <div className="flex flex-col">
                            <label className="text-xs text-gray-500 font-medium mb-1">End Month</label>
                            <input type="month" value={months.end_month} onChange={(e) => handleCustomMonthChange("end_month", e.target.value)} className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-sm" />
                        </div>
                    </div>
                )}
                
                {error && (
                    <div className="flex items-center gap-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 p-4 rounded-xl text-red-700 dark:text-red-400">
                        <AlertCircle className="w-5 h-5 flex-shrink-0" />
                        <p className="text-sm font-medium">{error}</p>
                    </div>
                )}
                
                {data && data.is_data_complete === false && !error && (
                    <div className="flex items-center gap-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800/50 p-4 rounded-xl text-amber-700 dark:text-amber-400">
                        <AlertCircle className="w-5 h-5 flex-shrink-0" />
                        <p className="text-sm font-medium">Incomplete Reporting Data. Please run Tally Sync for this period to view accurate records.</p>
                    </div>
                )}

                {isMissingStock && (
                    <div className="flex items-start gap-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 p-4 rounded-xl text-red-700 dark:text-red-400">
                        <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                        <div>
                            <p className="text-sm font-bold">{selectedStore === "Combined" ? "Combined P&L Incomplete" : "Stock data incomplete"}</p>
                            <p className="text-sm mt-1">Stock data is missing for: <span className="font-semibold capitalize">{missingStores.join(', ')}</span></p>
                        </div>
                    </div>
                )}
                
                {loading && !data && (
                    <div className="text-center py-12 text-gray-500">Loading P&L...</div>
                )}

                {activeData && (
                    <>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            {renderKPICard('Net Sales', activeData.revenue.net_sales, activePreviousData?.revenue.net_sales)}
                            {renderKPICard('Gross Profit', activeData.gross_profit, activePreviousData?.gross_profit)}
                            {renderKPICard('Net Profit', activeData.net_profit, activePreviousData?.net_profit)}
                        </div>
                        
                        <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden mt-6 max-w-4xl">
                            <div className="p-6 border-b border-gray-100 dark:border-gray-800">
                                <h2 className="text-xl font-bold text-gray-900 dark:text-white uppercase tracking-wider">Profit &amp; Loss Statement</h2>
                            </div>
                            
                            <div className="p-6">
                                <div className="space-y-6 text-sm">
                                    {/* Revenue */}
                                    <div>
                                        <h3 className="font-bold text-gray-900 dark:text-white mb-2 uppercase tracking-wide text-xs">Revenue</h3>
                                        <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                                            <span className="text-gray-700 dark:text-gray-300 pl-4">Net Sales</span>
                                            <span className="font-medium text-gray-900 dark:text-white">{formatCurrency(activeData.revenue.net_sales)}</span>
                                        </div>
                                    </div>
                                    
                                    {/* COGS */}
                                    <div>
                                        <h3 className="font-bold text-gray-900 dark:text-white mb-2 uppercase tracking-wide text-xs">Cost of Goods Sold</h3>
                                        <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                                            <span className="text-gray-700 dark:text-gray-300 pl-4">Opening Stock</span>
                                            <span className="text-gray-900 dark:text-gray-300">{formatCurrency(activeData.cost_of_goods_sold.opening_stock)}</span>
                                        </div>
                                        <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                                            <span className="text-gray-700 dark:text-gray-300 pl-4">Net Purchases</span>
                                            <span className="text-gray-900 dark:text-gray-300">{formatCurrency(activeData.cost_of_goods_sold.net_purchases)}</span>
                                        </div>
                                        <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                                            <span className="text-gray-700 dark:text-gray-300 pl-4">Less: Closing Stock</span>
                                            <span className="text-gray-900 dark:text-gray-300">{activeData.cost_of_goods_sold.closing_stock !== null ? `(${formatCurrency(activeData.cost_of_goods_sold.closing_stock)})` : '—'}</span>
                                        </div>
                                        <div className="flex justify-between py-2 border-b-2 border-gray-200 dark:border-gray-700 font-bold bg-gray-50/50 dark:bg-gray-800/50 pl-4 pr-1 mt-1">
                                            <span className="text-gray-900 dark:text-white">Cost of Goods Sold</span>
                                            <span className="text-gray-900 dark:text-white">{formatCurrency(activeData.cost_of_goods_sold.cogs)}</span>
                                        </div>
                                    </div>
                                    
                                    {/* Gross Profit */}
                                    <div className="flex justify-between py-3 border-b border-gray-300 dark:border-gray-600 font-black text-base bg-blue-50/30 dark:bg-blue-900/10 px-2 rounded-lg">
                                        <span className="text-gray-900 dark:text-white">Gross Profit</span>
                                        <span className="text-gray-900 dark:text-white">{formatCurrency(activeData.gross_profit)}</span>
                                    </div>

                                    {/* Expenses */}
                                    <div>
                                        <h3 className="font-bold text-gray-900 dark:text-white mb-2 uppercase tracking-wide text-xs">Expenses</h3>
                                        <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                                            <span className="text-gray-700 dark:text-gray-300 pl-4">Direct Expenses</span>
                                            <span className="text-gray-900 dark:text-gray-300">{activeData.expenses.direct_expenses !== 0 ? formatCurrency(activeData.expenses.direct_expenses) : '—'}</span>
                                        </div>
                                        <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                                            <span className="text-gray-700 dark:text-gray-300 pl-4">Indirect Expenses</span>
                                            <span className="text-gray-900 dark:text-gray-300">{activeData.expenses.indirect_expenses !== 0 ? formatCurrency(activeData.expenses.indirect_expenses) : '—'}</span>
                                        </div>
                                        <div className="flex justify-between py-2 border-b-2 border-gray-200 dark:border-gray-700 font-bold bg-gray-50/50 dark:bg-gray-800/50 pl-4 pr-1 mt-1">
                                            <span className="text-gray-900 dark:text-white">Total Expenses</span>
                                            <span className="text-gray-900 dark:text-white">{formatCurrency((activeData.expenses.direct_expenses || 0) + (activeData.expenses.indirect_expenses || 0))}</span>
                                        </div>
                                    </div>

                                    {/* Net Profit */}
                                    <div className="flex justify-between py-4 font-black text-lg bg-green-50/50 dark:bg-green-900/20 px-4 rounded-xl border border-green-100 dark:border-green-800/50">
                                        <span className="text-gray-900 dark:text-white uppercase tracking-wider">Net Profit</span>
                                        <span className="text-gray-900 dark:text-white">{formatCurrency(activeData.net_profit)}</span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {selectedStore === "Combined" && data.stores.length > 0 && (
                            <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden flex flex-col mt-6">
                                <div className="p-5 border-b border-gray-100 dark:border-gray-800">
                                    <h2 className="text-lg font-bold text-gray-900 dark:text-white">Store Comparison</h2>
                                </div>
                                <div className="overflow-x-auto">
                                    <table className="w-full text-left border-collapse min-w-[600px]">
                                        <thead>
                                            <tr className="border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Store</th>
                                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider text-right">Net Sales</th>
                                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider text-right">Purchases</th>
                                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider text-right">Gross Profit</th>
                                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider text-right">Net Profit</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                                            {data.stores.map((s, idx) => (
                                                <tr key={idx} className="hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors">
                                                    <td className="p-4 text-sm font-medium text-gray-900 dark:text-white flex items-center gap-2">
                                                        {s.store}
                                                        {s.missing_stores?.length > 0 && (
                                                            <span className="w-2 h-2 rounded-full bg-red-500" title="Missing Stock Data" />
                                                        )}
                                                    </td>
                                                    <td className="p-4 text-sm text-gray-600 dark:text-gray-300 text-right">{formatCurrency(s.revenue.net_sales)}</td>
                                                    <td className="p-4 text-sm text-gray-600 dark:text-gray-300 text-right">{formatCurrency(s.cost_of_goods_sold.net_purchases)}</td>
                                                    <td className="p-4 text-sm font-medium text-gray-900 dark:text-gray-100 text-right">{formatCurrency(s.gross_profit)}</td>
                                                    <td className="p-4 text-sm font-bold text-gray-900 dark:text-white text-right">{formatCurrency(s.net_profit)}</td>
                                                </tr>
                                            ))}
                                            <tr className="bg-gray-50 dark:bg-gray-800 border-t-2 border-gray-200 dark:border-gray-700">
                                                <td className="p-4 text-sm font-bold text-gray-900 dark:text-white">Combined</td>
                                                <td className="p-4 text-sm font-bold text-gray-900 dark:text-white text-right">{formatCurrency(data.combined.revenue.net_sales)}</td>
                                                <td className="p-4 text-sm font-bold text-gray-900 dark:text-white text-right">{formatCurrency(data.combined.cost_of_goods_sold.net_purchases)}</td>
                                                <td className="p-4 text-sm font-black text-gray-900 dark:text-white text-right">{formatCurrency(data.combined.gross_profit)}</td>
                                                <td className="p-4 text-sm font-black text-gray-900 dark:text-white text-right">{formatCurrency(data.combined.net_profit)}</td>
                                            </tr>
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
