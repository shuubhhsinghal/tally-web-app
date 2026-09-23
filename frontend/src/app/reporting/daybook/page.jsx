"use client";

import React, { useState, useEffect } from 'react';
import { AlertCircle, RefreshCw, Store, ChevronLeft, ChevronRight, Search, ArrowUpDown, X, ExternalLink, Filter } from 'lucide-react';
import ReportTabs from '@/components/layout/ReportTabs';
import { useAuth } from '@/context/AuthContext';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const PRESETS = [
    { label: "Today", value: "today" },
    { label: "Yesterday", value: "yesterday" },
    { label: "This Week", value: "week" },
    { label: "This Month", value: "month" },
    { label: "This Quarter", value: "quarter" },
    { label: "This Financial Year", value: "fy" },
    { label: "Custom", value: "custom" },
];

function formatCurrency(val) {
    if (val === undefined || val === null || val === 0) return "";
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        minimumFractionDigits: 2
    }).format(Math.abs(val));
}

function getPresetDates(preset) {
    const today = new Date();
    const y = today.getFullYear();
    const m = today.getMonth();
    const d = today.getDate();
    
    let start, end;
    
    if (preset === "today") {
        start = new Date();
        end = new Date();
    } else if (preset === "yesterday") {
        start = new Date(y, m, d - 1);
        end = new Date(y, m, d - 1);
    } else if (preset === "week") {
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

const formatDateWithWeekday = (yyyymmdd) => {
    if (!yyyymmdd || yyyymmdd.length !== 8) return yyyymmdd;
    const year = parseInt(yyyymmdd.substring(0, 4), 10);
    const month = parseInt(yyyymmdd.substring(4, 6), 10) - 1;
    const day = parseInt(yyyymmdd.substring(6, 8), 10);
    const date = new Date(year, month, day);
    
    const dayFormatter = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short' });
    const weekdayFormatter = new Intl.DateTimeFormat('en-GB', { weekday: 'short' });
    
    return `${dayFormatter.format(date)} · ${weekdayFormatter.format(date)}`;
};

const toInputDate = (yyyymmdd) => {
    if (!yyyymmdd) return "";
    return `${yyyymmdd.substring(0,4)}-${yyyymmdd.substring(4,6)}-${yyyymmdd.substring(6,8)}`;
};

export default function DaybookReport() {
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    
    const [preset, setPreset] = useState("month");
    const [dates, setDates] = useState(getPresetDates("month"));
    
    const { user } = useAuth();
    const lockedStore = user && !user.is_owner ? user.store_name : null;

    const [costCentres, setCostCentres] = useState([]);
    const [selectedStore, setSelectedStore] = useState(lockedStore || "");

    useEffect(() => {
        if (lockedStore) setSelectedStore(lockedStore);
    }, [lockedStore]);

    const [page, setPage] = useState(1);
    const [limit, setLimit] = useState(50);
    const [searchInput, setSearchInput] = useState("");
    const [search, setSearch] = useState("");
    const [sortBy, setSortBy] = useState("date_desc");

    // Debounce the search box -- typing updates searchInput immediately (so
    // the field itself feels responsive), but the actual `search` value that
    // triggers a fetch only updates 400ms after the user stops typing,
    // instead of firing a full network request on every keystroke.
    useEffect(() => {
        const timer = setTimeout(() => {
            setSearch(searchInput);
            setPage(1);
        }, 400);
        return () => clearTimeout(timer);
    }, [searchInput]);
    
    const [selectedVoucherId, setSelectedVoucherId] = useState(null);
    const [voucherDetails, setVoucherDetails] = useState(null);
    const [voucherLoading, setVoucherLoading] = useState(false);

    useEffect(() => {
        fetchCostCentres();
    }, []);
    
    useEffect(() => {
        if (preset !== "custom") {
            setDates(getPresetDates(preset));
            setPage(1);
        }
    }, [preset]);
    
    useEffect(() => {
        if (dates.start && dates.end) {
            fetchDaybook();
        }
    }, [dates, selectedStore, page, limit, search, sortBy]);

    useEffect(() => {
        if (selectedVoucherId) {
            fetchVoucherDetails(selectedVoucherId);
        } else {
            setVoucherDetails(null);
        }
    }, [selectedVoucherId]);

    const fetchCostCentres = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/reporting/inspect/cost-centres`);
            if (res.ok) {
                const json = await res.json();
                setCostCentres(json);
            }
        } catch (err) {}
    };

    const fetchDaybook = async () => {
        try {
            setLoading(true);
            const params = new URLSearchParams({
                start_date: dates.start,
                end_date: dates.end,
                page,
                limit,
                search,
                sort_by: sortBy
            });
            if (selectedStore) params.append("cost_centre", selectedStore);
            
            const res = await fetch(`${API_BASE}/api/reporting/daybook?${params.toString()}`);
            if (!res.ok) throw new Error("Failed to fetch Daybook");
            
            const json = await res.json();
            setData(json);
            setError(null);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const fetchVoucherDetails = async (id) => {
        try {
            setVoucherLoading(true);
            const res = await fetch(`${API_BASE}/api/reporting/vouchers/${id}`);
            if (!res.ok) throw new Error("Failed to fetch voucher details");
            const json = await res.json();
            setVoucherDetails(json);
        } catch (err) {
            console.error(err);
        } finally {
            setVoucherLoading(false);
        }
    };

    const handleCustomDateChange = (type, val) => {
        const stripped = val.replace(/-/g, '');
        setPreset("custom");
        setDates(prev => ({ ...prev, [type]: stripped }));
        setPage(1);
    };

    const handleSort = (key) => {
        if (sortBy === `${key}_desc`) {
            setSortBy(`${key}_asc`);
        } else {
            setSortBy(`${key}_desc`);
        }
    };

    const renderVoucherModal = () => {
        if (!selectedVoucherId) return null;
        
        return (
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm">
                <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden">
                    <div className="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-900">
                        <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                            {voucherDetails ? voucherDetails.voucher.voucher_type : 'Voucher'} Details
                        </h2>
                        <button onClick={() => setSelectedVoucherId(null)} className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors">
                            <X className="w-5 h-5" />
                        </button>
                    </div>
                    
                    <div className="p-6 overflow-y-auto flex-1">
                        {voucherLoading ? (
                            <div className="flex justify-center py-12">
                                <RefreshCw className="h-8 w-8 text-blue-500 animate-spin" />
                            </div>
                        ) : voucherDetails ? (
                            <div className="space-y-6">
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-6 bg-gray-50 dark:bg-gray-900/50 p-4 rounded-xl border border-gray-100 dark:border-gray-800">
                                    <div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400 font-medium uppercase tracking-wider mb-1">Date</p>
                                        <p className="font-semibold text-gray-900 dark:text-white">{toInputDate(voucherDetails.voucher.date)}</p>
                                    </div>
                                    <div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400 font-medium uppercase tracking-wider mb-1">Voucher No.</p>
                                        <p className="font-semibold text-gray-900 dark:text-white">{voucherDetails.voucher.voucher_number || '-'}</p>
                                    </div>
                                    <div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400 font-medium uppercase tracking-wider mb-1">Type</p>
                                        <p className="font-semibold text-blue-600 dark:text-blue-400">{voucherDetails.voucher.voucher_type}</p>
                                    </div>
                                    <div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400 font-medium uppercase tracking-wider mb-1">Party / Particulars</p>
                                        <p className="font-semibold text-gray-900 dark:text-white">{voucherDetails.voucher.party_ledger_name || '-'}</p>
                                    </div>
                                </div>
                                
                                {/* Extra fields (Reference, Bank) */}
                                {(voucherDetails.voucher.reference || voucherDetails.voucher.cheque_number) && (
                                    <div className="grid grid-cols-2 gap-4">
                                        {voucherDetails.voucher.reference && (
                                            <div className="bg-amber-50 dark:bg-amber-900/10 p-3 rounded-lg border border-amber-100 dark:border-amber-900/30">
                                                <p className="text-xs text-amber-600 dark:text-amber-500 font-medium uppercase mb-1">Supplier Ref / Bill No.</p>
                                                <p className="font-semibold text-amber-900 dark:text-amber-300">{voucherDetails.voucher.reference} {voucherDetails.voucher.reference_date ? `(${toInputDate(voucherDetails.voucher.reference_date)})` : ''}</p>
                                            </div>
                                        )}
                                        {voucherDetails.voucher.cheque_number && (
                                            <div className="bg-indigo-50 dark:bg-indigo-900/10 p-3 rounded-lg border border-indigo-100 dark:border-indigo-900/30">
                                                <p className="text-xs text-indigo-600 dark:text-indigo-500 font-medium uppercase mb-1">Bank Instrument</p>
                                                <p className="font-semibold text-indigo-900 dark:text-indigo-300">
                                                    {voucherDetails.voucher.cheque_number} 
                                                    {voucherDetails.voucher.cheque_date ? ` (${toInputDate(voucherDetails.voucher.cheque_date)})` : ''} 
                                                    {voucherDetails.voucher.bank_name ? ` - ${voucherDetails.voucher.bank_name}` : ''}
                                                </p>
                                            </div>
                                        )}
                                    </div>
                                )}

                                <div>
                                    <h3 className="font-bold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
                                        <ArrowUpDown className="w-4 h-4 text-gray-400" /> Accounting Entries
                                    </h3>
                                    <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden shadow-sm">
                                        <table className="w-full text-sm text-left">
                                            <thead className="bg-gray-50 dark:bg-gray-900 text-gray-500 dark:text-gray-400">
                                                <tr>
                                                    <th className="px-4 py-3 font-semibold uppercase tracking-wider">Particulars</th>
                                                    <th className="px-4 py-3 font-semibold uppercase tracking-wider">Cost Centre</th>
                                                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Debit</th>
                                                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Credit</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-gray-100 dark:divide-gray-800 bg-white dark:bg-gray-800">
                                                {voucherDetails.ledgers.map((l, idx) => {
                                                    const isDebit = l.is_deemed_positive === 1;
                                                    const ccNames = l.cost_centre_allocations?.map(c => c.cost_centre_name).join(', ') || '-';
                                                    return (
                                                        <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                                                            <td className="px-4 py-3 text-gray-900 dark:text-white font-medium">{l.ledger_name}</td>
                                                            <td className="px-4 py-3 text-gray-500 dark:text-gray-400">{ccNames}</td>
                                                            <td className="px-4 py-3 text-right text-gray-900 dark:text-white font-medium">{isDebit ? formatCurrency(Math.abs(l.amount)) : ''}</td>
                                                            <td className="px-4 py-3 text-right text-gray-900 dark:text-white font-medium">{!isDebit ? formatCurrency(Math.abs(l.amount)) : ''}</td>
                                                        </tr>
                                                    );
                                                })}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>

                                {voucherDetails.inventory && voucherDetails.inventory.length > 0 && (
                                    <div>
                                        <h3 className="font-bold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
                                            <Store className="w-4 h-4 text-gray-400" /> Inventory Entries
                                        </h3>
                                        <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden shadow-sm">
                                            <table className="w-full text-sm text-left">
                                                <thead className="bg-gray-50 dark:bg-gray-900 text-gray-500 dark:text-gray-400">
                                                    <tr>
                                                        <th className="px-4 py-3 font-semibold uppercase tracking-wider">Item Name</th>
                                                        <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Quantity</th>
                                                        <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Amount</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-100 dark:divide-gray-800 bg-white dark:bg-gray-800">
                                                    {voucherDetails.inventory.map((it, idx) => (
                                                        <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                                                            <td className="px-4 py-3 text-gray-900 dark:text-white">{it.stock_item_name}</td>
                                                            <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-300 font-medium">{Math.abs(it.billed_qty)}</td>
                                                            <td className="px-4 py-3 text-right font-medium text-gray-900 dark:text-white">{formatCurrency(Math.abs(it.amount))}</td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                )}
                                
                                {voucherDetails.voucher.narration && (
                                    <div className="bg-gray-50 dark:bg-gray-900/50 p-4 rounded-xl border border-gray-200 dark:border-gray-700">
                                        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Narration</p>
                                        <p className="text-sm text-gray-800 dark:text-gray-300 italic">"{voucherDetails.voucher.narration}"</p>
                                    </div>
                                )}
                            </div>
                        ) : null}
                    </div>
                </div>
            </div>
        );
    };

    const totalPages = data ? Math.ceil(data.total / limit) : 0;
    const offsetCalc = (page - 1) * limit + 1;

    return (
        <div className="w-full flex flex-col min-h-screen pb-20">
            <ReportTabs />
            <div className="p-6 max-w-7xl mx-auto space-y-6 w-full flex-1">
            {renderVoucherModal()}
            {error && (
                <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-4 rounded-xl text-sm border border-red-100 dark:border-red-900/30">
                    {error}
                </div>
            )}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold text-gray-900 dark:text-white tracking-tight">Daybook</h1>
                    <p className="text-gray-500 dark:text-gray-400 mt-1">Chronological record of all accounting transactions.</p>
                </div>
                
                <div className="flex flex-wrap items-center gap-3">
                    <div className="relative">
                        <Store className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                        {lockedStore ? (
                            <div className="pl-9 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-xl bg-white dark:bg-gray-800 text-sm shadow-sm">
                                {lockedStore}
                            </div>
                        ) : (
                        <select
                            value={selectedStore}
                            onChange={(e) => {setSelectedStore(e.target.value); setPage(1);}}
                            className="pl-9 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-xl bg-white dark:bg-gray-800 text-sm focus:ring-2 focus:ring-blue-500 shadow-sm appearance-none cursor-pointer"
                        >
                            <option value="">Combined (All Stores)</option>
                            <option value="Unallocated">Unallocated (Corporate)</option>
                            {costCentres.map(c => (
                                <option key={c.name} value={c.name}>{c.name}</option>
                            ))}
                        </select>
                        )}
                    </div>
                    
                    <div className="flex bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-xl p-1 shadow-sm">
                        {PRESETS.map(p => (
                            <button
                                key={p.value}
                                onClick={() => setPreset(p.value)}
                                className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${preset === p.value ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
                            >
                                {p.label}
                            </button>
                        ))}
                    </div>
                </div>
            </div>
            
            {preset === "custom" && (
                <div className="flex items-center gap-4 bg-gray-50 dark:bg-gray-800/50 p-4 rounded-xl border border-gray-200 dark:border-gray-800">
                    <div className="flex flex-col">
                        <label className="text-xs text-gray-500 font-medium mb-1">Start Date</label>
                        <input type="date" value={toInputDate(dates.start)} onChange={(e) => handleCustomDateChange("start", e.target.value)} className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-sm" />
                    </div>
                    <div className="flex flex-col">
                        <label className="text-xs text-gray-500 font-medium mb-1">End Date</label>
                        <input type="date" value={toInputDate(dates.end)} onChange={(e) => handleCustomDateChange("end", e.target.value)} className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-sm" />
                    </div>
                </div>
            )}
            
            <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden flex flex-col">
                <div className="p-5 border-b border-gray-100 dark:border-gray-800 flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <h2 className="text-lg font-bold text-gray-900 dark:text-white">Transaction Log</h2>
                    
                    <div className="flex items-center gap-3">
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                            <input
                                type="text"
                                placeholder="Search particulars, type, ref..."
                                value={searchInput}
                                onChange={(e) => setSearchInput(e.target.value)}
                                className="pl-9 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-900 text-sm w-full md:w-64 focus:ring-2 focus:ring-blue-500 outline-none"
                            />
                        </div>
                    </div>
                </div>
                
                <div className="overflow-x-auto">
                    <table className="w-full text-left border-collapse">
                        <thead>
                            <tr className="border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700" onClick={() => handleSort('date')}>
                                    <div className="flex items-center gap-1">Date <ArrowUpDown className="w-3 h-3" /></div>
                                </th>
                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Particulars</th>
                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Vch Type</th>
                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Vch No.</th>
                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700" onClick={() => handleSort('amount')}>
                                    <div className="flex items-center justify-end gap-1">Debit (In) <ArrowUpDown className="w-3 h-3" /></div>
                                </th>
                                <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider text-right">Credit (Out)</th>
                                {(!selectedStore || selectedStore === "Unallocated") && (
                                    <th className="p-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Store</th>
                                )}
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                            {loading ? (
                                <tr>
                                    <td colSpan={7} className="p-8 text-center text-gray-500"><RefreshCw className="w-6 h-6 animate-spin mx-auto text-blue-500" /></td>
                                </tr>
                            ) : data?.vouchers?.length > 0 ? (
                                data.vouchers.map((v) => (
                                    <tr
                                        key={v.id}
                                        onClick={() => !v.is_pending && setSelectedVoucherId(v.id)}
                                        title={v.is_pending ? "Not yet confirmed by Tally -- no details to view yet" : undefined}
                                        className={`transition-colors ${v.is_pending ? "opacity-70 cursor-default" : "hover:bg-blue-50 dark:hover:bg-blue-900/20 cursor-pointer"}`}
                                    >
                                        <td className="p-4 text-sm font-medium text-gray-900 dark:text-gray-100 whitespace-nowrap">
                                            {formatDateWithWeekday(v.date)}
                                        </td>
                                        <td className="p-4 text-sm text-gray-900 dark:text-gray-100">
                                            <div className="font-semibold">{v.party_ledger_name}</div>
                                            {v.reference && <div className="text-xs text-gray-500 mt-0.5">Ref: {v.reference}</div>}
                                            {v.cheque_number && <div className="text-xs text-gray-500 mt-0.5">Inst: {v.cheque_number}</div>}
                                        </td>
                                        <td className="p-4 text-sm font-medium text-blue-600 dark:text-blue-400">
                                            {v.voucher_type}
                                            {v.is_pending && (
                                                <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400 align-middle">
                                                    Pending
                                                </span>
                                            )}
                                        </td>
                                        <td className="p-4 text-sm text-gray-600 dark:text-gray-400">{v.voucher_number || '-'}</td>
                                        <td className="p-4 text-right font-medium text-gray-900 dark:text-gray-100">{formatCurrency(v.debit)}</td>
                                        <td className="p-4 text-right font-medium text-gray-900 dark:text-gray-100">{formatCurrency(v.credit)}</td>
                                        {(!selectedStore || selectedStore === "Unallocated") && (
                                            <td className="p-4 text-sm">
                                                <span className={`px-2.5 py-1 rounded-lg text-xs font-medium ${
                                                    v.store_display === 'Multiple' ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400' :
                                                    v.store_display === 'Unallocated' ? 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400' :
                                                    'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400'
                                                }`}>
                                                    {v.store_display}
                                                </span>
                                            </td>
                                        )}
                                    </tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={7} className="p-8 text-center text-gray-500 text-sm">No vouchers found for this period.</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
                
                <div className="p-4 border-t border-gray-100 dark:border-gray-800 flex items-center justify-between bg-gray-50/30 dark:bg-gray-900/30 mt-auto">
                    <span className="text-sm text-gray-500">
                        Showing {data?.vouchers?.length > 0 ? offsetCalc : 0} to {Math.min(page * limit, data?.total || 0)} of {data?.total || 0} transactions
                    </span>
                    <div className="flex items-center gap-2">
                        <button 
                            onClick={() => setPage(p => Math.max(1, p - 1))}
                            disabled={page === 1}
                            className="p-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-500 disabled:opacity-50 hover:bg-gray-100 dark:hover:bg-gray-700"
                        >
                            <ChevronLeft className="w-4 h-4" />
                        </button>
                        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Page {page} of {totalPages || 1}</span>
                        <button 
                            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                            disabled={page === totalPages || totalPages === 0}
                            className="p-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-500 disabled:opacity-50 hover:bg-gray-100 dark:hover:bg-gray-700"
                        >
                            <ChevronRight className="w-4 h-4" />
                        </button>
                    </div>
                </div>
            </div>
            </div>
        </div>
    );
}
