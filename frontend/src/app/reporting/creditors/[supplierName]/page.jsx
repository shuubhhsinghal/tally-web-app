"use client";

import React, { useState, useEffect } from 'react';
import { useRouter, useParams, useSearchParams } from 'next/navigation';
import { ArrowLeft, RefreshCw, Eye } from 'lucide-react';
import ReportTabs from '@/components/layout/ReportTabs';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function formatCurrency(val) {
    if (val === undefined || val === null) return "₹0.00";
    const absVal = Math.abs(val);
    const formatted = new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        minimumFractionDigits: 2
    }).format(absVal);
    
    if (val > 0) return `${formatted} Cr`;
    if (val < 0) return `${formatted} Dr`;
    return `${formatted} Cr`;
}

function formatDateDisplay(dStr) {
    if (!dStr) return '';
    // Expected YYYYMMDD
    if (dStr.length === 8) {
        return `${dStr.substring(6,8)}/${dStr.substring(4,6)}/${dStr.substring(0,4)}`;
    }
    return dStr;
}

export default function SupplierLedgerPage() {
    const router = useRouter();
    const params = useParams();
    const searchParams = useSearchParams();
    
    const supplierName = decodeURIComponent(params.supplierName);
    const startDate = searchParams.get('start_date');
    const endDate = searchParams.get('end_date');

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    
    const [selectedVoucher, setSelectedVoucher] = useState(null);
    const [voucherDetails, setVoucherDetails] = useState(null);
    const [voucherLoading, setVoucherLoading] = useState(false);

    useEffect(() => {
        if (supplierName && startDate && endDate) {
            fetchLedger();
        }
    }, [supplierName, startDate, endDate]);

    const fetchLedger = async () => {
        try {
            setLoading(true);
            const query = new URLSearchParams({ start_date: startDate, end_date: endDate });
            const res = await fetch(`${API_BASE}/api/reporting/creditors/${encodeURIComponent(supplierName)}?${query}`);
            if (!res.ok) throw new Error("Failed to fetch ledger");
            const d = await res.json();
            
            // Calculate chronological running balance
            let currentBal = d.period_opening;
            d.movements = d.movements.map(m => {
                currentBal += m.amount;
                return { ...m, running_balance: currentBal };
            });
            
            setData(d);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const fetchVoucherDetails = async (voucherId, type) => {
        setSelectedVoucher({ id: voucherId, type });
        try {
            setVoucherLoading(true);
            if (type === 'Purchase') {
                const res = await fetch(`${API_BASE}/api/reporting/purchases/bills/${voucherId}`);
                if (!res.ok) throw new Error("Failed to fetch bill details");
                setVoucherDetails(await res.json());
            } else {
                const res = await fetch(`${API_BASE}/api/reporting/vouchers/${voucherId}`);
                if (!res.ok) throw new Error("Failed to fetch voucher details");
                setVoucherDetails(await res.json());
            }
        } catch (err) {
            console.error(err);
        } finally {
            setVoucherLoading(false);
        }
    };

    const renderVoucherModal = () => {
        if (!selectedVoucher || (!voucherDetails && !voucherLoading)) return null;
        
        return (
            <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
                <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[90vh]">
                    <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
                        <h2 className="font-semibold text-gray-900 dark:text-white">
                            {selectedVoucher.type} Voucher Details
                        </h2>
                        <button onClick={() => { setSelectedVoucher(null); setVoucherDetails(null); }} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                            Close
                        </button>
                    </div>
                    
                    <div className="p-4 overflow-y-auto space-y-4 min-h-[200px]">
                        {voucherLoading ? (
                             <div className="flex justify-center py-8">
                                 <RefreshCw className="h-6 w-6 text-blue-500 animate-spin" />
                             </div>
                        ) : voucherDetails ? (
                            <>
                                <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                                    <div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400">Date</p>
                                        <p className="font-medium text-gray-900 dark:text-white">{formatDateDisplay(voucherDetails.voucher.date)}</p>
                                    </div>
                                    <div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400">Voucher No.</p>
                                        <p className="font-medium text-gray-900 dark:text-white">{voucherDetails.voucher.voucher_number || '-'}</p>
                                    </div>
                                    {selectedVoucher.type === 'Purchase' && voucherDetails.voucher.reference ? (
                                        <div>
                                            <p className="text-xs text-gray-500 dark:text-gray-400">Supplier Inv No.</p>
                                            <p className="font-medium text-gray-900 dark:text-white">{voucherDetails.voucher.reference}</p>
                                        </div>
                                    ) : null}
                                    <div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400">Type</p>
                                        <p className="font-medium text-gray-900 dark:text-white">{voucherDetails.voucher.voucher_type}</p>
                                    </div>
                                    <div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400">Party</p>
                                        <p className="font-medium text-gray-900 dark:text-white">{voucherDetails.voucher.party_ledger_name || '-'}</p>
                                    </div>
                                </div>

                                {selectedVoucher.type === 'Purchase' && voucherDetails.items && (
                                    <div className="mt-4">
                                        <h3 className="font-medium mb-2 text-gray-900 dark:text-white">Items</h3>
                                        <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
                                            <table className="w-full text-sm text-left">
                                                <thead className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400">
                                                    <tr>
                                                        <th className="px-4 py-2 font-medium">Item</th>
                                                        <th className="px-4 py-2 font-medium text-right">Qty</th>
                                                        <th className="px-4 py-2 font-medium text-right">Rate</th>
                                                        <th className="px-4 py-2 font-medium text-right">Amount</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                                                    {voucherDetails.items && voucherDetails.items.length > 0 ? (
                                                        voucherDetails.items.map((it, idx) => {
                                                            const qty = (it.billed_qty !== undefined && it.billed_qty !== null) ? Math.abs(it.billed_qty) : ((it.quantity !== undefined && it.quantity !== null) ? Math.abs(it.quantity) : 0);
                                                            const amt = (it.amount !== undefined && it.amount !== null) ? Math.abs(it.amount) : ((it.value !== undefined && it.value !== null) ? Math.abs(it.value) : 0);
                                                            const displayRate = it.rate ? it.rate : (qty > 0 ? (amt / qty).toFixed(2) : '—');
                                                            
                                                            return (
                                                                <tr key={idx} className="bg-white dark:bg-gray-800">
                                                                    <td className="px-4 py-2 text-gray-900 dark:text-white">{it.stock_item_name || it.item_name || '—'}</td>
                                                                    <td className="px-4 py-2 text-right text-gray-600 dark:text-gray-300">
                                                                        {qty > 0 ? qty : '—'}
                                                                    </td>
                                                                    <td className="px-4 py-2 text-right text-gray-600 dark:text-gray-300">
                                                                        {displayRate}
                                                                    </td>
                                                                    <td className="px-4 py-2 text-right font-medium text-gray-900 dark:text-white">
                                                                        {amt > 0 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(amt) : '—'}
                                                                    </td>
                                                                </tr>
                                                            );
                                                        })
                                                    ) : (
                                                        <tr className="bg-white dark:bg-gray-800">
                                                            <td colSpan="4" className="px-4 py-6 text-center text-gray-500 dark:text-gray-400">
                                                                No inventory items recorded
                                                            </td>
                                                        </tr>
                                                    )}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                )}

                                {selectedVoucher.type !== 'Purchase' && voucherDetails.ledgers && (
                                    <div className="mt-4">
                                        <h3 className="font-medium mb-2 text-gray-900 dark:text-white">Ledger Entries</h3>
                                        <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
                                            <table className="w-full text-sm text-left">
                                                <thead className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400">
                                                    <tr>
                                                        <th className="px-4 py-2 font-medium">Ledger</th>
                                                        <th className="px-4 py-2 font-medium text-right">Debit</th>
                                                        <th className="px-4 py-2 font-medium text-right">Credit</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                                                    {voucherDetails.ledgers.map((l, idx) => (
                                                        <tr key={idx} className="bg-white dark:bg-gray-800">
                                                            <td className="px-4 py-2 text-gray-900 dark:text-white">{l.ledger_name}</td>
                                                            <td className="px-4 py-2 text-right text-gray-600 dark:text-gray-300">
                                                                {l.is_deemed_positive === 1 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Math.abs(l.amount)) : '-'}
                                                            </td>
                                                            <td className="px-4 py-2 text-right text-gray-600 dark:text-gray-300">
                                                                {l.is_deemed_positive === 0 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Math.abs(l.amount)) : '-'}
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                )}
                            </>
                        ) : null}
                    </div>
                </div>
            </div>
        );
    };

    if (loading && !data) {
        return (
            <div className="p-6 max-w-5xl mx-auto flex justify-center items-center h-64">
                <RefreshCw className="h-8 w-8 text-blue-500 animate-spin" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-6 max-w-5xl mx-auto">
                <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-4 rounded-xl">
                    {error}
                </div>
            </div>
        );
    }

    return (
        <div className="w-full flex flex-col min-h-screen pb-20">
            <ReportTabs />
            <div className="p-6 max-w-5xl mx-auto space-y-6 w-full flex-1">
            <button 
                onClick={() => router.push('/reporting/creditors')}
                className="flex items-center text-sm font-medium text-blue-600 hover:text-blue-700"
            >
                <ArrowLeft className="w-4 h-4 mr-1" />
                Back to Creditors Overview
            </button>

            {data && (
                <>
                    {data.is_data_complete === false && (
                        <div className="bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 p-4 rounded-xl text-sm border border-amber-100 dark:border-amber-900/30">
                            Reporting data for this period may be incomplete -- run a Tally sync to make sure everything is up to date.
                        </div>
                    )}

                    <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-white dark:bg-gray-800 p-6 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
                        <div>
                            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{supplierName}</h1>
                            <p className="text-gray-500 dark:text-gray-400 text-sm mt-1">
                                Statement of Account ({startDate} to {endDate})
                            </p>
                        </div>
                        <div className="flex flex-col sm:flex-row gap-6 text-right">
                            <div>
                                <p className="text-xs text-gray-500 dark:text-gray-400 uppercase font-medium">Opening Balance</p>
                                <p className="text-lg font-bold text-gray-900 dark:text-white">{formatCurrency(data.period_opening)}</p>
                                {Math.abs(data.pending_opening_amount || 0) > 0.005 && (
                                    <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-0.5">
                                        incl. {formatCurrency(Math.abs(data.pending_opening_amount))} pending Tally confirmation
                                    </p>
                                )}
                            </div>
                            <div className="hidden sm:block w-px bg-gray-200 dark:bg-gray-700"></div>
                            <div>
                                <p className="text-xs text-gray-500 dark:text-gray-400 uppercase font-medium">Closing Balance</p>
                                <p className="text-lg font-bold text-blue-600 dark:text-blue-400">{formatCurrency(data.period_closing)}</p>
                                {Math.abs(data.pending_amount || 0) > 0.005 && (
                                    <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-0.5">
                                        incl. {formatCurrency(Math.abs(data.pending_amount))} pending Tally confirmation
                                    </p>
                                )}
                            </div>
                        </div>
                    </div>

                    <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
                        <div className="overflow-x-auto">
                            <table className="w-full text-left text-sm">
                                <thead className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-xs uppercase font-medium">
                                    <tr>
                                        <th className="px-6 py-4">Date</th>
                                        <th className="px-6 py-4">Particulars</th>
                                        <th className="px-6 py-4">Voucher No.</th>
                                        <th className="px-6 py-4">Supplier Inv. No.</th>
                                        <th className="px-6 py-4 text-right">Debit</th>
                                        <th className="px-6 py-4 text-right">Credit</th>
                                        <th className="px-6 py-4 text-right">Balance</th>
                                        <th className="px-6 py-4 text-center">Action</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                                    {/* Opening Balance Row */}
                                    <tr className="bg-gray-50/50 dark:bg-gray-800/30">
                                        <td className="px-6 py-4 text-gray-500 dark:text-gray-400 font-medium" colSpan="6">Opening Balance</td>
                                        <td className="px-6 py-4 text-right font-medium text-gray-900 dark:text-white">
                                            {formatCurrency(data.period_opening)}
                                            {Math.abs(data.pending_opening_amount || 0) > 0.005 && (
                                                <p className="text-[10px] font-normal text-amber-600 dark:text-amber-400 mt-0.5">
                                                    incl. {formatCurrency(Math.abs(data.pending_opening_amount))} pending
                                                </p>
                                            )}
                                        </td>
                                        <td></td>
                                    </tr>
                                    
                                    {data.movements.map((m, idx) => (
                                        <tr key={idx} className={`hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors ${m.is_pending ? 'bg-amber-50/50 dark:bg-amber-900/10' : ''}`}>
                                            <td className="px-6 py-4 whitespace-nowrap text-gray-900 dark:text-white">
                                                {formatDateDisplay(m.date)}
                                            </td>
                                            <td className="px-6 py-4 text-gray-900 dark:text-white">
                                                <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                                                    m.voucher_type === 'Purchase' ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300' :
                                                    m.voucher_type === 'Payment' ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300' :
                                                    'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'
                                                }`}>
                                                    {m.voucher_type}
                                                </span>
                                                {m.is_pending && (
                                                    <span className="inline-flex items-center px-2 py-0.5 ml-1 rounded text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
                                                        Pending
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-6 py-4 text-gray-600 dark:text-gray-400">
                                                {m.voucher_number || '-'}
                                            </td>
                                            <td className="px-6 py-4 text-gray-600 dark:text-gray-400">
                                                {m.reference || '-'}
                                            </td>
                                            <td className="px-6 py-4 text-right text-gray-600 dark:text-gray-300">
                                                {m.amount < 0 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Math.abs(m.amount)) : '-'}
                                            </td>
                                            <td className="px-6 py-4 text-right text-gray-600 dark:text-gray-300">
                                                {m.amount > 0 ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(m.amount) : '-'}
                                            </td>
                                            <td className="px-6 py-4 text-right font-medium text-gray-900 dark:text-white">
                                                {formatCurrency(m.running_balance)}
                                            </td>
                                            <td className="px-6 py-4 text-center">
                                                {m.voucher_id ? (
                                                    <button
                                                        onClick={() => fetchVoucherDetails(m.voucher_id, m.voucher_type)}
                                                        className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 p-1"
                                                    >
                                                        <Eye className="w-4 h-4 inline" />
                                                    </button>
                                                ) : (
                                                    <span className="text-gray-300 dark:text-gray-600 text-xs" title="Not yet synced to Tally">--</span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                    
                                    {data.movements.length === 0 && (
                                        <tr>
                                            <td colSpan="8" className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                                                No transactions found in this period.
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </>
            )}
            
            {renderVoucherModal()}
            </div>
        </div>
    );
}
