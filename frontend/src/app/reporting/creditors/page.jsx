"use client";

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { RefreshCw, Calendar, Search, ArrowUpDown } from 'lucide-react';
import ReportTabs from '@/components/layout/ReportTabs';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const PRESETS = [
    { label: "This Week", value: "week" },
    { label: "This Month", value: "month" },
    { label: "This Quarter", value: "quarter" },
    { label: "This Financial Year", value: "fy" },
    { label: "Custom", value: "custom" },
];

function formatCurrency(val) {
    if (val === undefined || val === null) return "₹0.00";
    const absVal = Math.abs(val);
    const formatted = new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        minimumFractionDigits: 2
    }).format(absVal);
    
    // Credit is positive (Cr). Debit is negative (Dr).
    if (val > 0) return `${formatted} Cr`;
    if (val < 0) return `${formatted} Dr`;
    return `${formatted} Cr`; // 0 is usually shown as Cr
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
        end = new Date(start);
        end.setDate(start.getDate() + 6);
    } else if (preset === "month") {
        start = new Date(y, m, 1);
        end = new Date(y, m + 1, 0);
    } else if (preset === "quarter") {
        const q = Math.floor(m / 3);
        start = new Date(y, q * 3, 1);
        end = new Date(y, q * 3 + 3, 0);
    } else if (preset === "fy") {
        const startY = m < 3 ? y - 1 : y;
        start = new Date(startY, 3, 1);
        end = new Date(startY + 1, 2, 31);
    }
    
    const fmt = d => d ? d.toISOString().split('T')[0] : '';
    return { start: fmt(start), end: fmt(end) };
}

export default function CreditorsReport() {
    const router = useRouter();
    const [preset, setPreset] = useState("month");
    const [dateRange, setDateRange] = useState(getPresetDates("month"));
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [searchTerm, setSearchTerm] = useState("");
    
    const [sortConfig, setSortConfig] = useState({ key: 'supplier_name', direction: 'asc' });

    const handlePresetChange = (e) => {
        const p = e.target.value;
        setPreset(p);
        if (p !== "custom") {
            setDateRange(getPresetDates(p));
        }
    };

    const fetchData = async () => {
        try {
            setLoading(true);
            setError(null);
            
            const params = new URLSearchParams({
                start_date: dateRange.start,
                end_date: dateRange.end
            });
            
            const res = await fetch(`${API_BASE}/api/reporting/creditors?${params}`);
            if (!res.ok) throw new Error("Failed to fetch creditors data");
            const d = await res.json();
            setData(d);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (dateRange.start && dateRange.end) {
            fetchData();
        }
    }, [dateRange.start, dateRange.end]);

    const handleSort = (key) => {
        let direction = 'asc';
        if (sortConfig.key === key && sortConfig.direction === 'asc') {
            direction = 'desc';
        }
        setSortConfig({ key, direction });
    };

    const sortedData = [...data].sort((a, b) => {
        if (a[sortConfig.key] < b[sortConfig.key]) {
            return sortConfig.direction === 'asc' ? -1 : 1;
        }
        if (a[sortConfig.key] > b[sortConfig.key]) {
            return sortConfig.direction === 'asc' ? 1 : -1;
        }
        return 0;
    });

    const filteredData = sortedData.filter(item => 
        item.supplier_name.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const SortIcon = ({ columnKey }) => {
        if (sortConfig.key !== columnKey) return <ArrowUpDown className="h-4 w-4 text-gray-400 ml-1" />;
        return <ArrowUpDown className={`h-4 w-4 ml-1 ${sortConfig.direction === 'asc' ? 'text-blue-500' : 'text-blue-500 rotate-180'}`} />;
    };

    return (
        <div className="w-full flex flex-col min-h-screen pb-20">
            <ReportTabs />
            <div className="p-6 max-w-7xl mx-auto space-y-6 w-full flex-1">
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Creditors Ledger</h1>
                    <p className="text-gray-500 dark:text-gray-400 text-sm mt-1">
                        Supplier outstanding balances and chronological ledger movements
                    </p>
                </div>
                
                <div className="flex flex-wrap gap-4 items-center bg-white dark:bg-gray-800 p-2 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
                    <div className="flex items-center gap-2 px-2">
                        <Calendar className="h-4 w-4 text-gray-400" />
                        <select
                            value={preset}
                            onChange={handlePresetChange}
                            className="bg-transparent text-sm text-gray-700 dark:text-gray-200 focus:outline-none cursor-pointer"
                        >
                            {PRESETS.map(p => (
                                <option key={p.value} value={p.value}>{p.label}</option>
                            ))}
                        </select>
                    </div>

                    <div className="h-4 w-px bg-gray-200 dark:bg-gray-700"></div>

                    <div className="flex items-center gap-2">
                        <input
                            type="date"
                            value={dateRange.start}
                            onChange={e => {
                                setDateRange(prev => ({ ...prev, start: e.target.value }));
                                setPreset("custom");
                            }}
                            className="bg-transparent text-sm text-gray-700 dark:text-gray-200 focus:outline-none"
                        />
                        <span className="text-gray-400">to</span>
                        <input
                            type="date"
                            value={dateRange.end}
                            onChange={e => {
                                setDateRange(prev => ({ ...prev, end: e.target.value }));
                                setPreset("custom");
                            }}
                            className="bg-transparent text-sm text-gray-700 dark:text-gray-200 focus:outline-none"
                        />
                    </div>

                    <button 
                        onClick={fetchData}
                        disabled={loading}
                        className="p-2 bg-gray-50 dark:bg-gray-700 hover:bg-gray-100 dark:hover:bg-gray-600 rounded-lg transition-colors border border-gray-200 dark:border-gray-600 disabled:opacity-50"
                    >
                        <RefreshCw className={`h-4 w-4 text-gray-600 dark:text-gray-300 ${loading ? 'animate-spin' : ''}`} />
                    </button>
                </div>
            </div>

            {error && (
                <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-4 rounded-xl text-sm border border-red-100 dark:border-red-900/30">
                    {error}
                </div>
            )}

            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
                <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="font-semibold text-gray-900 dark:text-white">Supplier Balances</h2>
                    <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                        <input
                            type="text"
                            placeholder="Search suppliers..."
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                            className="pl-9 pr-4 py-2 border border-gray-200 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500 w-64"
                        />
                    </div>
                </div>
                
                <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                        <thead className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-xs uppercase font-medium">
                            <tr>
                                <th className="px-6 py-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors" onClick={() => handleSort('supplier_name')}>
                                    <div className="flex items-center">Supplier <SortIcon columnKey="supplier_name" /></div>
                                </th>
                                <th className="px-6 py-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors" onClick={() => handleSort('period_opening')}>
                                    <div className="flex items-center justify-end">Opening Balance <SortIcon columnKey="period_opening" /></div>
                                </th>
                                <th className="px-6 py-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors" onClick={() => handleSort('purchases')}>
                                    <div className="flex items-center justify-end">Purchases <SortIcon columnKey="purchases" /></div>
                                </th>
                                <th className="px-6 py-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors" onClick={() => handleSort('returns')}>
                                    <div className="flex items-center justify-end">Returns <SortIcon columnKey="returns" /></div>
                                </th>
                                <th className="px-6 py-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors" onClick={() => handleSort('payments')}>
                                    <div className="flex items-center justify-end">Payments <SortIcon columnKey="payments" /></div>
                                </th>
                                <th className="px-6 py-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors" onClick={() => handleSort('other_adjustments')}>
                                    <div className="flex items-center justify-end">Adjustments <SortIcon columnKey="other_adjustments" /></div>
                                </th>
                                <th className="px-6 py-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors" onClick={() => handleSort('period_closing')}>
                                    <div className="flex items-center justify-end">Closing Balance <SortIcon columnKey="period_closing" /></div>
                                </th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                            {filteredData.length === 0 ? (
                                <tr>
                                    <td colSpan="7" className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                                        No suppliers found
                                    </td>
                                </tr>
                            ) : (
                                filteredData.map((row, idx) => (
                                    <tr 
                                        key={idx} 
                                        onClick={() => router.push(`/reporting/creditors/${encodeURIComponent(row.supplier_name)}?start_date=${dateRange.start}&end_date=${dateRange.end}`)}
                                        className="hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors cursor-pointer"
                                    >
                                        <td className="px-6 py-4 font-medium text-gray-900 dark:text-white">
                                            {row.supplier_name}
                                        </td>
                                        <td className="px-6 py-4 text-right text-gray-600 dark:text-gray-300">
                                            {formatCurrency(row.period_opening)}
                                        </td>
                                        <td className="px-6 py-4 text-right text-gray-600 dark:text-gray-300">
                                            {row.purchases > 0 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(row.purchases) : '-'}
                                        </td>
                                        <td className="px-6 py-4 text-right text-gray-600 dark:text-gray-300">
                                            {row.returns > 0 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(row.returns) : '-'}
                                        </td>
                                        <td className="px-6 py-4 text-right text-gray-600 dark:text-gray-300">
                                            {row.payments > 0 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(row.payments) : '-'}
                                        </td>
                                        <td className="px-6 py-4 text-right text-gray-600 dark:text-gray-300">
                                            {row.other_adjustments !== 0 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Math.abs(row.other_adjustments)) : '-'}
                                        </td>
                                        <td className="px-6 py-4 text-right font-medium text-gray-900 dark:text-white">
                                            {formatCurrency(row.period_closing)}
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
            </div>
        </div>
    );
}
