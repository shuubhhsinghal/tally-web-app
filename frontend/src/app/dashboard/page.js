'use client';

import React, { useState, useEffect } from 'react';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { EmptyState } from '@/components/ui/EmptyState';
import { Button } from '@/components/ui/Button';
import { TextArea } from '@/components/ui/TextArea';
import { useUI } from '@/context/UIContext';
import { Receipt, MoveRight, Building, Package, Landmark, AlertTriangle, RefreshCcw, Save, Trash2, Pencil, X, Database } from 'lucide-react';
import Link from 'next/link';

const TransactionPreview = ({ operation, payloadStr }) => {
  if (!payloadStr) return <EmptyState title="No Payload" message="There is no data to preview." />;
  let data;
  try {
    data = JSON.parse(payloadStr);
  } catch (e) {
    return <EmptyState title="Invalid Data" message="The payload is not valid JSON." />;
  }

  if (operation.includes('VOUCHER')) {
    if (data.items) {
      // Purchase Item Invoice
      const subtotal = data.items.reduce((sum, item) => sum + (item.amount || 0), 0);
      const total = subtotal + (data.cgst || 0) + (data.sgst || 0) + (data.igst || 0) + (data.rounding_off || 0);
      return (
        <div className="space-y-4">
          <Card className="flex flex-col gap-3">
            <div className="flex justify-between items-start border-b border-gray-100 dark:border-gray-800 pb-3">
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase">Supplier</p>
                <p className="text-sm font-bold text-gray-900 dark:text-white">{data.supplier || data.name}</p>
              </div>
              <div className="text-right">
                <p className="text-xs font-bold text-gray-400 uppercase">Date</p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.date || data.tally_date}</p>
              </div>
            </div>
            <div className="flex justify-between items-start">
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase">Inv No</p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.invoice_number}</p>
              </div>
              <div className="text-right">
                <p className="text-xs font-bold text-gray-400 uppercase">Cost Center</p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.cost_center}</p>
              </div>
            </div>
          </Card>

          <h3 className="text-xs font-bold text-gray-500 uppercase px-1">{data.items.length} Line Items</h3>
          <div className="space-y-2">
            {data.items.map((item, idx) => (
              <Card key={idx} className="p-3">
                <div className="flex justify-between items-start">
                  <p className="text-sm font-bold text-gray-900 dark:text-white">{item.mapped_name || item.name}</p>
                  <p className="text-sm font-bold text-gray-900 dark:text-white">₹ {item.amount?.toFixed(2)}</p>
                </div>
                <p className="text-xs font-medium text-gray-500 mt-1">{item.qty} {item.uom} × ₹{item.rate}</p>
              </Card>
            ))}
          </div>

          <Card className="flex flex-col gap-2">
            <div className="flex justify-between text-xs text-gray-500 font-medium"><span>Subtotal</span><span>₹ {subtotal.toFixed(2)}</span></div>
            {(data.cgst > 0 || data.sgst > 0) && <div className="flex justify-between text-xs text-gray-500 font-medium"><span>CGST + SGST</span><span>₹ {(data.cgst + data.sgst).toFixed(2)}</span></div>}
            {data.igst > 0 && <div className="flex justify-between text-xs text-gray-500 font-medium"><span>IGST</span><span>₹ {data.igst.toFixed(2)}</span></div>}
            {data.rounding_off !== 0 && <div className="flex justify-between text-xs text-gray-500 font-medium"><span>Rounding</span><span>{data.rounding_off}</span></div>}
            <div className="h-px bg-gray-100 dark:bg-gray-800 my-1" />
            <div className="flex justify-between text-lg font-black text-gray-900 dark:text-white"><span>Total</span><span>₹ {total.toFixed(2)}</span></div>
          </Card>
        </div>
      );
    } else if (data.ledger && data.amount !== undefined && !data.items) {
      // Sales Voucher
      return (
        <Card className="flex flex-col gap-3">
          <div className="flex justify-between items-start border-b border-gray-100 dark:border-gray-800 pb-3">
            <div><p className="text-xs font-bold text-gray-400 uppercase">Customer</p><p className="text-sm font-bold text-gray-900 dark:text-white">{data.ledger}</p></div>
            <div className="text-right"><p className="text-xs font-bold text-gray-400 uppercase">Amount</p><p className="text-lg font-black text-teal-600">₹ {Number(data.amount).toFixed(2)}</p></div>
          </div>
          {data.cost_center && <div><p className="text-xs font-bold text-gray-400 uppercase">Cost Center</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.cost_center}</p></div>}
          {data.narration && <div><p className="text-xs font-bold text-gray-400 uppercase">Narration</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.narration}</p></div>}
        </Card>
      );
    } else if ((data.debit_ledger && data.credit_ledger) || (data.from_account && data.to_account)) {
      // Payment or Transfer
      const debit = data.debit_ledger || data.to_account;
      const credit = data.credit_ledger || data.from_account;
      return (
        <Card className="flex flex-col gap-3">
          <div className="flex justify-between items-start border-b border-gray-100 dark:border-gray-800 pb-3">
            <div><p className="text-xs font-bold text-gray-400 uppercase">Amount</p><p className="text-2xl font-black text-teal-600">₹ {Number(data.amount).toFixed(2)}</p></div>
            <div className="text-right"><p className="text-xs font-bold text-gray-400 uppercase">Date</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.date || 'Today'}</p></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><p className="text-xs font-bold text-gray-400 uppercase">Debit (To)</p><p className="text-sm font-bold text-gray-900 dark:text-white">{debit}</p></div>
            <div><p className="text-xs font-bold text-gray-400 uppercase">Credit (From)</p><p className="text-sm font-bold text-gray-900 dark:text-white">{credit}</p></div>
          </div>
          {data.cost_center && <div><p className="text-xs font-bold text-gray-400 uppercase">Cost Center</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.cost_center}</p></div>}
          {data.narration && <div><p className="text-xs font-bold text-gray-400 uppercase">Narration</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.narration}</p></div>}
        </Card>
      );
    } else if (data.source_godown && data.destination_godown) {
      // Stock Transfer
      return (
        <Card className="flex flex-col gap-3">
          <div className="flex justify-between items-center border-b border-gray-100 dark:border-gray-800 pb-3">
            <div className="flex-1"><p className="text-xs font-bold text-gray-400 uppercase">From</p><p className="text-sm font-bold text-gray-900 dark:text-white">{data.source_godown}</p></div>
            <MoveRight className="w-5 h-5 text-gray-400 mx-2" />
            <div className="flex-1 text-right"><p className="text-xs font-bold text-gray-400 uppercase">To</p><p className="text-sm font-bold text-gray-900 dark:text-white">{data.destination_godown}</p></div>
          </div>
          <div><p className="text-xs font-bold text-gray-400 uppercase">Item</p><p className="text-sm font-bold text-gray-900 dark:text-white">{data.item_name}</p></div>
          <div className="grid grid-cols-2 gap-4">
            <div><p className="text-xs font-bold text-gray-400 uppercase">Quantity</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.qty}</p></div>
            <div><p className="text-xs font-bold text-gray-400 uppercase">Date</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.date || 'Today'}</p></div>
          </div>
          {data.narration && <div><p className="text-xs font-bold text-gray-400 uppercase">Narration</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.narration}</p></div>}
        </Card>
      );
    } else if (Array.isArray(data)) {
      // Bank Statement multiple vouchers
      return (
        <div className="space-y-4">
          <h3 className="text-xs font-bold text-gray-500 uppercase px-1">{data.length} Bank Transactions</h3>
          <div className="space-y-2">
            {data.map((txn, idx) => (
              <Card key={idx} className="p-3 flex flex-col gap-2">
                <div className="flex justify-between">
                  <p className="text-sm font-bold text-gray-900 dark:text-white">{txn.target_ledger}</p>
                  <p className={`text-sm font-bold ${txn.withdrawal > 0 ? 'text-red-600' : 'text-green-600'}`}>
                    {txn.withdrawal > 0 ? `- ₹${txn.withdrawal}` : `+ ₹${txn.deposit}`}
                  </p>
                </div>
                <div className="flex justify-between text-xs text-gray-500">
                  <span>{txn.date}</span>
                  <span>{txn.cost_center || 'No CC'}</span>
                </div>
              </Card>
            ))}
          </div>
        </div>
      );
    }
  }

  // Fallback
  return (
    <Card className="flex flex-col gap-2">
      <p className="text-sm font-medium text-gray-700 dark:text-gray-300 text-center py-4">
        Preview not available for this operation type.<br />Please use the Raw Data view.
      </p>
    </Card>
  );
};


export default function Dashboard() {
  const { showToast, showConfirmDialog } = useUI();
  const [stats, setStats] = useState({ queue_count: 0, cache_count: 0, tally_online: false });
  const [activities, setActivities] = useState([]);
  const [hiddenActivityIds, setHiddenActivityIds] = useState(new Set());
  const [mounted, setMounted] = useState(false);

  const [selectedItem, setSelectedItem] = useState(null);
  const [itemDetails, setItemDetails] = useState({ payload: '', xml_data: '' });
  const [isSaving, setIsSaving] = useState(false);

  // NEW: view toggle ('preview' or 'raw')
  const [viewMode, setViewMode] = useState('preview');
  const [isEditMode, setIsEditMode] = useState(false);
  const [editPayload, setEditPayload] = useState(null);

  const fetchStats = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/dashboard/stats');
      if (res.ok) {
        const data = await res.json();
        setStats(data);
        localStorage.setItem('dash_stats', JSON.stringify(data));
      }

      const actRes = await fetch('http://127.0.0.1:8000/api/dashboard/activity');
      if (actRes.ok) {
        const data = await actRes.json();
        setActivities(data);
        localStorage.setItem('dash_activity', JSON.stringify(data));
      }
    } catch (e) {
      console.error("Failed to load dashboard data", e);
    }
  };

  useEffect(() => {
    setMounted(true);

    // Load cached data instantly (0ms) so the page is not blank
    try {
      const cachedStats = localStorage.getItem('dash_stats');
      const cachedActivity = localStorage.getItem('dash_activity');
      if (cachedStats) setStats(JSON.parse(cachedStats));
      if (cachedActivity) setActivities(JSON.parse(cachedActivity));
    } catch { }

    // Then refresh from backend in background
    fetchStats();
    const interval = setInterval(() => {
      if (!selectedItem) fetchStats();
    }, 5000);
    return () => clearInterval(interval);
  }, [selectedItem]);

  const getStatus = (dbStatus) => {
    if (dbStatus === 'PENDING') return 'waiting';
    if (dbStatus === 'SYNCED') return 'synced';
    return 'failed';
  };

  const getIconType = (opType) => {
    if (!opType) return 'sale';
    if (opType.includes('VOUCHER')) return 'sale';
    if (opType.includes('LEDGER') || opType.includes('ITEM')) return 'bank';
    return 'sale';
  };

  const getIcon = (type) => {
    switch (type) {
      case 'sale': return <Receipt className="w-5 h-5 text-blue-500" />;
      case 'purchase': return <Package className="w-5 h-5 text-purple-500" />;
      case 'stock': return <MoveRight className="w-5 h-5 text-indigo-500" />;
      case 'bank': return <Landmark className="w-5 h-5 text-teal-500" />;
      case 'transfer': return <Building className="w-5 h-5 text-green-500" />;
      default: return <Receipt className="w-5 h-5 text-gray-500" />;
    }
  };

  const handleCardClick = async (id) => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/dashboard/activity/${id}`);
      if (res.ok) {
        const data = await res.json();
        setSelectedItem(data);
        setItemDetails({ payload: data.payload || '', xml_data: data.xml_data || '' });
        setViewMode('preview');
        setIsEditMode(false);
        setEditPayload(null);
      } else {
        showToast("Failed to fetch item details", "error");
      }
    } catch (e) {
      showToast("Error connecting to server", "error");
    }
  };

  const handleStartEdit = () => {
    try {
      setEditPayload(JSON.parse(itemDetails.payload));
      setIsEditMode(true);
    } catch (e) {
      showToast("Cannot edit: payload is invalid JSON", "error");
    }
  };

  const handleRebuildSave = async () => {
    setIsSaving(true);
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/dashboard/activity/${selectedItem.id}/rebuild`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload: editPayload })
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "Rebuild failed");
      showToast("Changes saved and XML rebuilt!");
      setIsEditMode(false);
      setEditPayload(null);
      setSelectedItem(null);
      fetchStats();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveItem = async () => {
    setIsSaving(true);
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/dashboard/activity/${selectedItem.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(itemDetails)
      });
      if (!res.ok) throw new Error("Update failed");

      showToast("Item updated and set to Pending");
      setSelectedItem(null);
      fetchStats();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleRetryItem = async () => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/dashboard/activity/${selectedItem.id}/retry`, { method: 'POST' });
      if (!res.ok) throw new Error("Retry failed");

      showToast("Item queued for retry");
      setSelectedItem(null);
      fetchStats();
    } catch (e) {
      showToast(e.message, 'error');
    }
  };

  const handleDeleteItem = async () => {
    showConfirmDialog({
      title: "Delete Transaction",
      message: "Are you sure you want to delete this from the queue? This cannot be undone.",
      confirmText: "Delete",
      cancelText: "Cancel",
      danger: true,
      onConfirm: async () => {
        try {
          const res = await fetch(`http://127.0.0.1:8000/api/dashboard/activity/${selectedItem.id}`, { method: 'DELETE' });
          if (!res.ok) throw new Error("Delete failed");

          showToast("Item deleted");
          setSelectedItem(null);
          fetchStats();
        } catch (e) {
          showToast(e.message, 'error');
        }
      }
    });
  };

  if (!mounted) return null;

  if (selectedItem) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-24">
        <TopBar title="Transaction Details" showBack onBack={() => setSelectedItem(null)} />

        <div className="max-w-md mx-auto p-4 space-y-4 mt-4">
          <Card className="flex flex-col gap-3">
            <div className="flex justify-between items-start">
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase tracking-wider">Operation</p>
                <p className="text-sm font-bold text-gray-900 dark:text-gray-100">{selectedItem.operation_type}</p>
              </div>
              <StatusBadge status={getStatus(selectedItem.status)} />
            </div>

            <div>
              <p className="text-xs font-bold text-gray-400 uppercase tracking-wider">Description</p>
              <p className="text-sm text-gray-700 dark:text-gray-300">{selectedItem.description}</p>
            </div>
          </Card>

          {selectedItem.error_message && (
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-900/50 p-4 rounded-xl flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-bold text-red-800 dark:text-red-400">Tally Error</p>
                <p className="text-xs font-medium text-red-600 dark:text-red-300 mt-1 break-words">
                  {selectedItem.error_message}
                </p>
              </div>
            </div>
          )}

          {/* Toggle Control */}
          <div className="flex p-1 bg-gray-200 dark:bg-gray-800 rounded-lg">
            <button
              onClick={() => setViewMode('preview')}
              className={`flex-1 py-1.5 text-sm font-bold rounded-md transition-all ${viewMode === 'preview'
                ? 'bg-white dark:bg-gray-700 text-teal-600 dark:text-teal-400 shadow-sm'
                : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
                }`}
            >
              Preview
            </button>
            <button
              onClick={() => setViewMode('raw')}
              className={`flex-1 py-1.5 text-sm font-bold rounded-md transition-all ${viewMode === 'raw'
                ? 'bg-white dark:bg-gray-700 text-teal-600 dark:text-teal-400 shadow-sm'
                : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
                }`}
            >
              Raw Data
            </button>
          </div>

          {viewMode === 'preview' ? (
            isEditMode && editPayload && editPayload.items ? (
              // --- Inline Edit Mode for Purchase Vouchers (PENDING only) ---
              <div className="space-y-4">
                <Card className="flex flex-col gap-3">
                  <div className="flex gap-3">
                    <div className="flex-1">
                      <p className="text-xs font-bold text-gray-400 uppercase mb-1">Supplier</p>
                      <input value={editPayload.supplier || ''} onChange={e => setEditPayload({ ...editPayload, supplier: e.target.value })} className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500" />
                    </div>
                    <div className="flex-1">
                      <p className="text-xs font-bold text-gray-400 uppercase mb-1">Invoice No</p>
                      <input value={editPayload.invoice_number || ''} onChange={e => setEditPayload({ ...editPayload, invoice_number: e.target.value })} className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500" />
                    </div>
                  </div>
                </Card>

                <h3 className="text-xs font-bold text-gray-500 uppercase px-1">Line Items</h3>
                {editPayload.items.map((item, idx) => (
                  <Card key={idx} className="flex flex-col gap-3 p-3">
                    <p className="text-xs font-bold text-gray-900 dark:text-white">{item.mapped_name || item.name}</p>
                    <div className="grid grid-cols-3 gap-2">
                      <div>
                        <p className="text-[10px] font-bold text-gray-400 uppercase mb-1">Qty</p>
                        <input type="number" step="0.01" value={item.qty} onChange={e => { const items = [...editPayload.items]; items[idx] = { ...items[idx], qty: parseFloat(e.target.value) || 0, amount: (parseFloat(e.target.value) || 0) * items[idx].rate }; setEditPayload({ ...editPayload, items }); }} className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500" />
                      </div>
                      <div>
                        <p className="text-[10px] font-bold text-gray-400 uppercase mb-1">Rate</p>
                        <input type="number" step="0.01" value={item.rate} onChange={e => { const items = [...editPayload.items]; items[idx] = { ...items[idx], rate: parseFloat(e.target.value) || 0, amount: items[idx].qty * (parseFloat(e.target.value) || 0) }; setEditPayload({ ...editPayload, items }); }} className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500" />
                      </div>
                      <div>
                        <p className="text-[10px] font-bold text-gray-400 uppercase mb-1">Amount</p>
                        <input type="number" step="0.01" value={item.amount} onChange={e => { const items = [...editPayload.items]; items[idx] = { ...items[idx], amount: parseFloat(e.target.value) || 0 }; setEditPayload({ ...editPayload, items }); }} className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500" />
                      </div>
                    </div>
                  </Card>
                ))}

                <Card className="flex flex-col gap-2">
                  <div className="grid grid-cols-3 gap-2">
                    {[['cgst', 'CGST'], ['sgst', 'SGST'], ['igst', 'IGST']].map(([key, label]) => (
                      <div key={key}>
                        <p className="text-[10px] font-bold text-gray-400 uppercase mb-1">{label}</p>
                        <input type="number" step="0.01" value={editPayload[key] || 0} onChange={e => setEditPayload({ ...editPayload, [key]: parseFloat(e.target.value) || 0 })} className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500" />
                      </div>
                    ))}
                  </div>
                  <div className="h-px bg-gray-100 dark:bg-gray-800 my-1" />
                  <div className="flex justify-between text-base font-black text-gray-900 dark:text-white px-1">
                    <span>New Total</span>
                    <span>₹ {((editPayload.items.reduce((s, i) => s + (i.amount || 0), 0)) + (editPayload.cgst || 0) + (editPayload.sgst || 0) + (editPayload.igst || 0) + (editPayload.rounding_off || 0)).toFixed(2)}</span>
                  </div>
                </Card>
              </div>
            ) : (
              <TransactionPreview operation={selectedItem.operation_type} payloadStr={itemDetails.payload} />
            )
          ) : (
            <Card className="flex flex-col gap-4">
              <TextArea
                label="JSON Payload"
                value={itemDetails.payload}
                onChange={e => setItemDetails({ ...itemDetails, payload: e.target.value })}
                className="font-mono text-xs"
              />

              <TextArea
                label="Tally XML Data"
                value={itemDetails.xml_data}
                onChange={e => setItemDetails({ ...itemDetails, xml_data: e.target.value })}
                className="font-mono text-xs min-h-[300px]"
              />
            </Card>
          )}

          <div className="fixed bottom-16 left-0 right-0 p-4 bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-800 z-50 flex gap-2">
            {/* Delete: only for non-SYNCED */}
            {selectedItem.status !== 'SYNCED' && (
              <Button variant="danger" onClick={handleDeleteItem} className="flex-1">
                <Trash2 className="w-5 h-5" /> Delete Transaction
              </Button>
            )}

            {/* Retry for FAILED */}
            {selectedItem.status === 'FAILED' && !isEditMode && (
              <Button variant="secondary" onClick={handleRetryItem} className="flex-1 border-teal-600 text-teal-600 hover:bg-teal-50">
                <RefreshCcw className="w-4 h-4 mr-2" /> Retry
              </Button>
            )}

            {/* Edit button: PENDING items in preview mode only */}
            {selectedItem.status === 'PENDING' && viewMode === 'preview' && !isEditMode && (
              <Button variant="secondary" onClick={handleStartEdit} className="flex-1">
                <Pencil className="w-4 h-4 mr-2" /> Edit
              </Button>
            )}

            {/* Cancel + Save when in edit mode */}
            {isEditMode && (
              <>
                <Button variant="secondary" onClick={() => { setIsEditMode(false); setEditPayload(null); }} className="flex-1">
                  <X className="w-4 h-4 mr-2" /> Cancel
                </Button>
                <Button onClick={handleRebuildSave} disabled={isSaving} className="flex-1">
                  <Save className="w-4 h-4 mr-2" /> {isSaving ? 'Saving...' : 'Save & Rebuild'}
                </Button>
              </>
            )}

            {/* Raw data save */}
            {viewMode === 'raw' && !isEditMode && (
              <Button onClick={handleSaveItem} disabled={isSaving} className="flex-1">
                <Save className="w-4 h-4 mr-2" /> Save Changes
              </Button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar title="Mom's Pride" />

      <div className="max-w-md mx-auto p-4 space-y-6 mt-4">

        {/* Summary Card */}
        <Card className="flex justify-between items-center px-6 py-5 bg-white dark:bg-gray-800">
          <div className="flex flex-col items-center">
            <span className="text-3xl font-black text-amber-500">{stats.queue_count}</span>
            <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 mt-1 uppercase tracking-wider">Waiting</span>
          </div>
          <div className="w-px h-10 bg-gray-100 dark:bg-gray-700" />
          <div className="flex flex-col items-center">
            <span className="text-3xl font-black text-red-500">
              {activities.filter(a => a.status === 'FAILED').length}
            </span>
            <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 mt-1 uppercase tracking-wider">Failed</span>
          </div>
        </Card>

        {/* Quick Links */}
        <div className="flex gap-3">
          <Link href="/masters" className="flex-1">
            <Card className="flex items-center justify-center gap-2 py-3 hover:ring-2 hover:ring-teal-500/50 transition-all cursor-pointer bg-white dark:bg-gray-800">
              <Database className="w-4 h-4 text-teal-600" />
              <span className="text-sm font-bold text-gray-900 dark:text-gray-100">Masters Overview</span>
            </Card>
          </Link>
        </div>

        {/* Recent Activity */}
        <div className="space-y-3">
          <div className="flex justify-between items-center px-1">
            <h2 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Recent Activity</h2>
            <button 
              onClick={() => {
                const idsToHide = activities
                  .filter(a => getStatus(a.status) === 'failed' || getStatus(a.status) === 'synced')
                  .map(a => a.id);
                if (idsToHide.length > 0) {
                  setHiddenActivityIds(prev => new Set([...prev, ...idsToHide]));
                }
              }}
              className="text-[10px] font-bold uppercase tracking-wider text-teal-600 dark:text-teal-400 hover:text-teal-700 hover:underline"
            >
              Clear Finished
            </button>
          </div>

          {(() => {
            const visibleActivities = activities.filter(a => !hiddenActivityIds.has(a.id));
            if (visibleActivities.length === 0) {
              return <EmptyState title="No recent activity" message="Nothing has been recorded yet today." />;
            }
            return (
              <div className="flex flex-col gap-3">
                {visibleActivities.map((activity, idx) => (
                <Card
                  key={idx}
                  onClick={() => handleCardClick(activity.id)}
                  className="flex items-center gap-4 p-4 cursor-pointer hover:ring-2 hover:ring-teal-500/50 transition-all active:scale-[0.98]"
                >
                  <div className="w-10 h-10 rounded-full bg-gray-50 dark:bg-gray-700 flex items-center justify-center shrink-0">
                    {getIcon(getIconType(activity.operation_type))}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-bold text-gray-900 dark:text-gray-100 truncate">
                      {activity.description || activity.operation_type}
                    </p>
                    <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mt-0.5">
                      {new Date(activity.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </p>
                  </div>
                  <StatusBadge status={getStatus(activity.status)} />
                </Card>
              ))}
            </div>
          );
        })()}
        </div>

      </div>
    </div>
  );
}
