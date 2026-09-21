'use client';

import { useState, useEffect } from 'react';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { Button } from '@/components/ui/Button';
import { TextArea } from '@/components/ui/TextArea';
import { useUI } from '@/context/UIContext';
import { AlertTriangle, RefreshCcw, Save, Trash2, Pencil, X } from 'lucide-react';
import { getStatus } from './activityHelpers';
import { TransactionPreview } from './TransactionPreview';
import { SALES_LEDGERS } from '@/utils/salesLedgers';

const yyyymmddToInputDate = (val) => {
  const s = String(val || '');
  return s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : '';
};

// A payload can be edited with a proper form only when its shape is recognized.
const isRebuildableShape = (payload) => {
  if (!payload) return false;
  return 'items' in payload || ('ledger' in payload && 'amount' in payload);
};

export const TransactionDetailView = ({ itemId, onBack, onMutated }) => {
  const { showToast, showConfirmDialog } = useUI();
  const [item, setItem] = useState(null);
  const [itemDetails, setItemDetails] = useState({ payload: '', xml_data: '' });
  const [isSaving, setIsSaving] = useState(false);
  const [viewMode, setViewMode] = useState('preview');
  const [isEditMode, setIsEditMode] = useState(false);
  const [editPayload, setEditPayload] = useState(null);
  const [stores, setStores] = useState(["Mahagun", "Vvip", "Gulshan"]);

  useEffect(() => {
    fetch('/api/sales/metadata')
      .then(res => res.json())
      .then(data => { if (Array.isArray(data.stores)) setStores(data.stores); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`/api/dashboard/activity/${itemId}`);
        if (res.ok) {
          const data = await res.json();
          if (cancelled) return;
          setItem(data);
          setItemDetails({ payload: data.payload || '', xml_data: data.xml_data || '' });
          setViewMode('preview');
          setIsEditMode(false);
          setEditPayload(null);
        } else {
          showToast("Failed to fetch item details", "error");
        }
      } catch (e) {
        if (!cancelled) showToast("Error connecting to server", "error");
      }
    };
    load();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemId]);

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
      const res = await fetch(`/api/dashboard/activity/${item.id}/rebuild`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload: editPayload })
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "Rebuild failed");
      showToast("Changes saved and XML rebuilt!");
      setIsEditMode(false);
      setEditPayload(null);
      onMutated?.();
      onBack();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveItem = async () => {
    setIsSaving(true);
    try {
      const res = await fetch(`/api/dashboard/activity/${item.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(itemDetails)
      });
      if (!res.ok) throw new Error("Update failed");

      showToast("Item updated and set to Pending");
      onMutated?.();
      onBack();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleRetryItem = async () => {
    try {
      const res = await fetch(`/api/dashboard/activity/${item.id}/retry`, { method: 'POST' });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "Retry failed");

      showToast(body.message || "Item queued for retry");
      onMutated?.();
      onBack();
    } catch (e) {
      showToast(e.message, 'error');
    }
  };

  const handleDeleteItem = async () => {
    showConfirmDialog({
      title: "Delete Item?",
      message: "Are you sure you want to permanently delete this item from the queue?",
      danger: true,
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/dashboard/activity/${item.id}`, { method: 'DELETE' });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(body.detail || "Delete failed");

          showToast("Item deleted");
          onMutated?.();
          onBack();
        } catch (err) {
          showToast(err.message || "Failed to delete", "error");
        }
      }
    });
  };

  if (!item) return null;

  let isDeliveryUncertain = false;
  let isRebuildable = false;
  try {
    const parsedPayload = JSON.parse(item.payload || '{}');
    isDeliveryUncertain = !!parsedPayload.delivery_uncertain;
    isRebuildable = isRebuildableShape(parsedPayload);
  } catch { }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-24">
      <TopBar title="Transaction Details" showBack onBack={onBack} />

      <div className="max-w-md mx-auto p-4 space-y-4 mt-4">
        <Card className="flex flex-col gap-3">
          <div className="flex justify-between items-start">
            <div>
              <p className="text-xs font-bold text-gray-400 uppercase tracking-wider">Operation</p>
              <p className="text-sm font-bold text-gray-900 dark:text-gray-100">{item.operation_type}</p>
            </div>
            <StatusBadge status={getStatus(item.status)} />
          </div>

          <div>
            <p className="text-xs font-bold text-gray-400 uppercase tracking-wider">Description</p>
            <p className="text-sm text-gray-700 dark:text-gray-300">{item.description}</p>
          </div>
        </Card>

        {item.error_message && (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-900/50 p-4 rounded-xl flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-red-800 dark:text-red-400">Tally Error</p>
              <p className="text-xs font-medium text-red-600 dark:text-red-300 mt-1 break-words">
                {item.error_message}
              </p>
            </div>
          </div>
        )}

        {/* Toggle Control -- only shown for types without a proper edit form
            (Payment/Transfer/Stock Transfer/Bank Statement), where Raw Data is
            the only way to change anything. Sales and Purchase Item invoices
            have a real Edit form now, so the toggle would just be clutter. */}
        {!isRebuildable && (
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
        )}

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
          ) : isEditMode && editPayload && 'ledger' in editPayload && 'amount' in editPayload ? (
            // --- Inline Edit Mode for Sales entries (PENDING only) ---
            <Card className="flex flex-col gap-4">
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase mb-1">Customer / Ledger</p>
                <select
                  value={editPayload.ledger || ''}
                  onChange={e => setEditPayload({ ...editPayload, ledger: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500"
                >
                  {SALES_LEDGERS.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase mb-1">Amount</p>
                <input
                  type="number" step="0.01" value={editPayload.amount}
                  onChange={e => setEditPayload({ ...editPayload, amount: parseFloat(e.target.value) || 0 })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500"
                />
              </div>
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase mb-1">Store</p>
                <select
                  value={editPayload.store || ''}
                  onChange={e => setEditPayload({ ...editPayload, store: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500"
                >
                  <option value="" disabled>Select store...</option>
                  {stores.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase mb-1">Date</p>
                <input
                  type="date" value={yyyymmddToInputDate(editPayload.tally_date)}
                  onChange={e => setEditPayload({ ...editPayload, tally_date: e.target.value.replace(/-/g, '') })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500"
                />
              </div>
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase mb-1">Notes</p>
                <textarea
                  value={editPayload.narration || ''}
                  onChange={e => setEditPayload({ ...editPayload, narration: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500 min-h-[80px]"
                />
              </div>
            </Card>
          ) : (
            <TransactionPreview operation={item.operation_type} payloadStr={itemDetails.payload} />
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

        <div className="fixed bottom-[80px] left-0 right-0 p-4 bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-800 z-50 flex gap-2">
          {/* Delete: available for PENDING or FAILED, hidden while editing, and
              blocked when delivery is unconfirmed -- deleting an ambiguous send
              could permanently lose the only record of a voucher that may
              already exist in Tally (the "Verify in Tally First" indicator
              below already explains why). */}
          {(item.status === 'FAILED' || item.status === 'PENDING') && !isEditMode && !isDeliveryUncertain && (
            <Button variant="danger" onClick={handleDeleteItem} className="flex-1">
              <Trash2 className="w-5 h-5" /> Delete
            </Button>
          )}

          {/* Retry for FAILED, unless delivery to Tally is unconfirmed (would risk a duplicate) */}
          {item.status === 'FAILED' && !isEditMode && !isDeliveryUncertain && (
            <Button variant="secondary" onClick={handleRetryItem} className="flex-1 border-teal-600 text-teal-600 hover:bg-teal-50">
              <RefreshCcw className="w-4 h-4 mr-2" /> Retry
            </Button>
          )}
          {item.status === 'FAILED' && !isEditMode && isDeliveryUncertain && (
            <Button variant="secondary" disabled className="flex-1 opacity-60 cursor-not-allowed">
              <AlertTriangle className="w-4 h-4 mr-2" /> Verify in Tally First
            </Button>
          )}

          {/* Edit button: PENDING items in preview mode only, and only for shapes
              with a real edit form (purchase item invoices, sales entries) --
              other types would silently show nothing if given a way in. Also
              blocked when delivery is unconfirmed, same as Retry -- editing and
              resending an ambiguous send risks creating a duplicate voucher. */}
          {item.status === 'PENDING' && viewMode === 'preview' && !isEditMode && isRebuildable && !isDeliveryUncertain && (
            <Button variant="secondary" onClick={handleStartEdit} className="flex-1">
              <Pencil className="w-4 h-4 mr-2" /> Edit
            </Button>
          )}
          {item.status === 'PENDING' && viewMode === 'preview' && !isEditMode && isRebuildable && isDeliveryUncertain && (
            <Button variant="secondary" disabled className="flex-1 opacity-60 cursor-not-allowed">
              <AlertTriangle className="w-4 h-4 mr-2" /> Verify in Tally First
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
};
