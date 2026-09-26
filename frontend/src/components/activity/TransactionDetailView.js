'use client';

import { useState, useEffect } from 'react';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { Button } from '@/components/ui/Button';
import { TextArea } from '@/components/ui/TextArea';
import { useUI } from '@/context/UIContext';
import { AlertTriangle, RefreshCcw, Save, Trash2, Pencil, X } from 'lucide-react';
import { getStatus } from './activityHelpers';
import { TransactionPreview } from './TransactionPreview';
import { SALES_LEDGERS } from '@/utils/salesLedgers';

function fmtMoney2(v) {
  const n = Number(v || 0);
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const yyyymmddToInputDate = (val) => {
  const s = String(val || '');
  return s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : '';
};

// A payload can be edited with a proper form only when its shape is recognized.
const isRebuildableShape = (payload) => {
  if (!payload) return false;
  return 'items' in payload || ('ledger' in payload && 'amount' in payload);
};

function formatDetailDate(v) {
  if (!v) return null;
  const s = String(v);
  let d;
  if (/^\d{8}$/.test(s)) d = new Date(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T00:00:00`);
  else if (/^\d{4}-\d{2}-\d{2}/.test(s)) d = new Date(`${s.slice(0, 10)}T00:00:00`);
  else return s;
  if (isNaN(d.getTime())) return s;
  return d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' });
}

function formatSavedTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso + (iso.includes('Z') ? '' : 'Z'));
  if (isNaN(d.getTime())) return iso;
  return `${d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })} on this phone`;
}

// Mirrors backend/routers/dashboard.py's _parse_activity_row -- this view
// gets the raw payload (not the pre-parsed list row), so the same
// description-prefix mapping is repeated here to get a clean type/title/
// amount for the sheet header.
function getHeaderInfo(item, payload) {
  const desc = item.description || '';
  const num = (k) => { const n = parseFloat(payload[k]); return isNaN(n) ? null : n; };

  if (desc.startsWith('Sales:')) {
    const ledger = payload.ledger || '';
    return { typeLabel: 'Sale', title: ledger.trim().toLowerCase() === 'cash' ? 'Cash sale (walk-in)' : ledger, amount: num('amount') };
  }
  if (desc.startsWith('Purchase Item Invoice:')) {
    const items = payload.items || [];
    const itemsTotal = items.reduce((s, i) => s + (parseFloat(i.amount) || 0), 0);
    const gst = (parseFloat(payload.cgst) || 0) + (parseFloat(payload.sgst) || 0) + (parseFloat(payload.igst) || 0) + (parseFloat(payload.rounding_off) || 0);
    return { typeLabel: 'Purchase', title: payload.supplier, amount: -(itemsTotal + gst) };
  }
  if (desc.startsWith('Purchase Invoice:')) {
    return { typeLabel: 'Purchase', title: payload.supplier, amount: num('amount') != null ? -num('amount') : null };
  }
  if (desc.startsWith('Purchase Return (Debit Note):')) {
    const items = (payload.adjustment || {}).items || [];
    const amt = items.reduce((s, i) => s + (parseFloat(i.qty) || 0) * (parseFloat(i.rate) || 0), 0);
    return { typeLabel: 'Purchase return', title: payload.supplier, amount: amt };
  }
  if (desc.startsWith('Payment:')) {
    return { typeLabel: 'Payment', title: payload.debit_ledger, amount: num('amount') != null ? -num('amount') : null };
  }
  if (desc.startsWith('Loan Received:')) {
    return { typeLabel: 'Loan received', title: payload.lender_name, amount: num('principal_amount') };
  }
  if (desc.startsWith('Bank Stmt Transfer:')) {
    return { typeLabel: 'Transfer', title: desc.replace('Bank Stmt Transfer:', '').trim() || 'Transfer', amount: null };
  }
  if (desc.startsWith('Bank Stmt:')) {
    const bankLedger = payload.bank_ledger_name;
    const isReceipt = payload.debit_ledger === bankLedger;
    const party = isReceipt ? payload.credit_ledger : payload.debit_ledger;
    const amt = num('amount');
    return { typeLabel: isReceipt ? 'Receipt' : 'Payment', title: party, amount: amt != null ? (isReceipt ? amt : -amt) : null };
  }
  return { typeLabel: (item.operation_type || '').replace(/_/g, ' '), title: desc || item.operation_type, amount: null };
}

// The Date field is named differently per voucher type's own payload shape
// (tally_date for the app's own forms, date for extraction/import-derived
// payloads) -- checked in a fixed order rather than guessed.
function getVoucherDate(payload) {
  return payload.tally_date || payload.date || payload.invoice_date || null;
}

export const TransactionDetailView = ({ itemId, onClose, onMutated }) => {
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

  const inputCls = "w-full px-3 py-2 text-sm rounded-md border border-divider bg-transparent text-text outline-none focus:border-accent transition-colors";
  const labelCls = "text-[11px] tracking-[0.08em] uppercase text-neutral-600 mb-1";

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
      onClose();
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
      onClose();
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
      onClose();
    } catch (e) {
      showToast(e.message, 'error');
    }
  };

  const handleConfirmNotDelivered = () => {
    showConfirmDialog({
      title: "Confirm you've checked Tally",
      message: "Delivery to Tally was left unconfirmed for this transaction — it may or may not have actually reached Tally. Only continue if you've opened Tally yourself and confirmed this voucher is NOT there. This unlocks Delete/Retry/Edit again; it doesn't retry or delete anything by itself.",
      danger: true,
      confirmText: "I've checked — unlock",
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/dashboard/activity/${item.id}/confirm-not-delivered`, { method: 'POST' });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(body.detail || "Failed to unlock");
          showToast(body.message || "Unlocked");
          const res2 = await fetch(`/api/dashboard/activity/${item.id}`);
          if (res2.ok) setItem(await res2.json());
        } catch (err) {
          showToast(err.message || "Failed to unlock", "error");
        }
      }
    });
  };

  const handleDeleteItem = async () => {
    showConfirmDialog({
      title: "Delete item?",
      message: "Are you sure you want to permanently delete this item from the queue?",
      danger: true,
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/dashboard/activity/${item.id}`, { method: 'DELETE' });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(body.detail || "Delete failed");

          showToast("Item deleted");
          onMutated?.();
          onClose();
        } catch (err) {
          showToast(err.message || "Failed to delete", "error");
        }
      }
    });
  };

  let payload = {};
  let isDeliveryUncertain = false;
  let isRebuildable = false;
  if (item) {
    try {
      payload = JSON.parse(item.payload || '{}');
      isDeliveryUncertain = !!payload.delivery_uncertain;
      isRebuildable = isRebuildableShape(payload);
    } catch { }
  }
  const info = item ? getHeaderInfo(item, payload) : { typeLabel: '', title: '', amount: null };

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={onClose} />
      <div className="fixed inset-x-0 bottom-0 sm:inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 pointer-events-none">
        <div
          className="bg-surface border-t sm:border border-divider rounded-t-2xl sm:rounded-lg shadow-lg w-full sm:max-w-lg max-h-[85vh] flex flex-col pointer-events-auto animate-in slide-in-from-bottom-full sm:zoom-in-95"
          onClick={e => e.stopPropagation()}
        >
          <div className="w-12 h-1.5 bg-divider rounded-full mx-auto mt-3 sm:hidden" />

          {!item ? (
            <div className="px-6 py-4">
              <div className="flex items-center justify-end">
                <button onClick={onClose} className="text-neutral-500 hover:text-text transition-colors"><X className="w-5 h-5" /></button>
              </div>
              <p className="text-sm text-neutral-600 text-center py-8">Loading&hellip;</p>
            </div>
          ) : (
            <>
              <div className="flex items-start justify-between gap-3 px-6 py-4 border-b border-divider">
                <div className="min-w-0">
                  <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">{info.typeLabel}</div>
                  <h3 className="font-heading font-semibold text-xl truncate">{info.title || 'Transaction'}</h3>
                </div>
                <StatusBadge status={getStatus(item.status)} className="shrink-0" />
              </div>

              <div className="overflow-y-auto flex-1 px-6 py-4">
                {isEditMode && editPayload && editPayload.items ? (
                  // --- Inline Edit Mode for Purchase Vouchers (PENDING only) ---
                  <div className="flex flex-col gap-4">
                    <div className="flex gap-3">
                      <div className="flex-1">
                        <p className={labelCls}>Supplier</p>
                        <input value={editPayload.supplier || ''} onChange={e => setEditPayload({ ...editPayload, supplier: e.target.value })} className={inputCls} />
                      </div>
                      <div className="flex-1">
                        <p className={labelCls}>Invoice no.</p>
                        <input value={editPayload.invoice_number || ''} onChange={e => setEditPayload({ ...editPayload, invoice_number: e.target.value })} className={inputCls} />
                      </div>
                    </div>

                    <div>
                      <p className={labelCls}>Line items</p>
                      <div className="flex flex-col gap-3">
                        {editPayload.items.map((lineItem, idx) => (
                          <div key={idx} className="border border-divider rounded-md p-3 flex flex-col gap-2">
                            <p className="text-sm">{lineItem.mapped_name || lineItem.name}</p>
                            <div className="grid grid-cols-3 gap-2">
                              <div>
                                <p className="text-[10px] tracking-[0.06em] uppercase text-neutral-600 mb-1">Qty</p>
                                <input type="number" step="0.01" value={lineItem.qty} onChange={e => { const items = [...editPayload.items]; items[idx] = { ...items[idx], qty: parseFloat(e.target.value) || 0, amount: (parseFloat(e.target.value) || 0) * items[idx].rate }; setEditPayload({ ...editPayload, items }); }} className={`${inputCls} px-2 py-1.5`} />
                              </div>
                              <div>
                                <p className="text-[10px] tracking-[0.06em] uppercase text-neutral-600 mb-1">Rate</p>
                                <input type="number" step="0.01" value={lineItem.rate} onChange={e => { const items = [...editPayload.items]; items[idx] = { ...items[idx], rate: parseFloat(e.target.value) || 0, amount: items[idx].qty * (parseFloat(e.target.value) || 0) }; setEditPayload({ ...editPayload, items }); }} className={`${inputCls} px-2 py-1.5`} />
                              </div>
                              <div>
                                <p className="text-[10px] tracking-[0.06em] uppercase text-neutral-600 mb-1">Amount</p>
                                <input type="number" step="0.01" value={lineItem.amount} onChange={e => { const items = [...editPayload.items]; items[idx] = { ...items[idx], amount: parseFloat(e.target.value) || 0 }; setEditPayload({ ...editPayload, items }); }} className={`${inputCls} px-2 py-1.5`} />
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="flex flex-col gap-2">
                      <div className="grid grid-cols-3 gap-2">
                        {[['cgst', 'CGST'], ['sgst', 'SGST'], ['igst', 'IGST']].map(([key, label]) => (
                          <div key={key}>
                            <p className="text-[10px] tracking-[0.06em] uppercase text-neutral-600 mb-1">{label}</p>
                            <input type="number" step="0.01" value={editPayload[key] || 0} onChange={e => setEditPayload({ ...editPayload, [key]: parseFloat(e.target.value) || 0 })} className={`${inputCls} px-2 py-1.5`} />
                          </div>
                        ))}
                      </div>
                      <div className="flex justify-between items-baseline border-t border-text pt-2 mt-1">
                        <span className="font-heading font-semibold text-base">New total</span>
                        <span className="font-heading font-semibold text-lg">{fmtMoney2((editPayload.items.reduce((s, i) => s + (i.amount || 0), 0)) + (editPayload.cgst || 0) + (editPayload.sgst || 0) + (editPayload.igst || 0) + (editPayload.rounding_off || 0))}</span>
                      </div>
                    </div>
                  </div>
                ) : isEditMode && editPayload && 'ledger' in editPayload && 'amount' in editPayload ? (
                  // --- Inline Edit Mode for Sales entries (PENDING only) ---
                  <div className="flex flex-col gap-4">
                    <div>
                      <p className={labelCls}>Customer / ledger</p>
                      <select value={editPayload.ledger || ''} onChange={e => setEditPayload({ ...editPayload, ledger: e.target.value })} className={inputCls}>
                        {SALES_LEDGERS.map(l => <option key={l} value={l}>{l}</option>)}
                      </select>
                    </div>
                    <div>
                      <p className={labelCls}>Amount</p>
                      <input type="number" step="0.01" value={editPayload.amount} onChange={e => setEditPayload({ ...editPayload, amount: parseFloat(e.target.value) || 0 })} className={inputCls} />
                    </div>
                    <div>
                      <p className={labelCls}>Store</p>
                      <select value={editPayload.store || ''} onChange={e => setEditPayload({ ...editPayload, store: e.target.value })} className={inputCls}>
                        <option value="" disabled>Select store&hellip;</option>
                        {stores.map(s => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </div>
                    <div>
                      <p className={labelCls}>Date</p>
                      <input type="date" value={yyyymmddToInputDate(editPayload.tally_date)} onChange={e => setEditPayload({ ...editPayload, tally_date: e.target.value.replace(/-/g, '') })} className={inputCls} />
                    </div>
                    <div>
                      <p className={labelCls}>Notes</p>
                      <textarea value={editPayload.narration || ''} onChange={e => setEditPayload({ ...editPayload, narration: e.target.value })} className={`${inputCls} min-h-[80px] resize-y`} />
                    </div>
                  </div>
                ) : (
                  <>
                    {info.amount != null && (
                      <div className="font-heading text-3xl leading-tight mb-3 [font-feature-settings:'tnum']">
                        {info.amount < 0 ? '− ' : ''}{fmtMoney2(Math.abs(info.amount))}
                      </div>
                    )}

                    {item.status === 'FAILED' && item.error_message && (
                      <div className="border border-accent rounded-md p-3 mb-3 flex items-start gap-2.5">
                        <AlertTriangle className="w-4 h-4 text-accent-700 shrink-0 mt-0.5" />
                        <div className="min-w-0">
                          <p className="text-[13px] font-medium text-accent-700">Tally rejected this entry</p>
                          <p className="text-[13px] text-accent-700 mt-0.5 break-words">{item.error_message}</p>
                        </div>
                      </div>
                    )}

                    {viewMode === 'raw' ? (
                      <div className="flex flex-col gap-4">
                        <TextArea label="JSON payload" value={itemDetails.payload} onChange={e => setItemDetails({ ...itemDetails, payload: e.target.value })} className="font-mono text-xs" />
                        <TextArea label="Tally XML data" value={itemDetails.xml_data} onChange={e => setItemDetails({ ...itemDetails, xml_data: e.target.value })} className="font-mono text-xs min-h-[300px]" />
                      </div>
                    ) : (
                      <div className="flex flex-col">
                        <div className="flex justify-between items-baseline gap-3 py-2.5 border-t border-divider">
                          <span className="text-[13px] text-neutral-700 shrink-0">Date</span>
                          <span className="text-[15px] text-right">{formatDetailDate(getVoucherDate(payload)) || '-'}</span>
                        </div>
                        <div className="flex justify-between items-baseline gap-3 py-2.5 border-t border-divider">
                          <span className="text-[13px] text-neutral-700 shrink-0">Saved</span>
                          <span className="text-[15px] text-right">{formatSavedTime(item.created_at)}</span>
                        </div>
                        <TransactionPreview operation={item.operation_type} payloadStr={itemDetails.payload} />
                      </div>
                    )}
                  </>
                )}
              </div>

              <div className={`px-6 py-4 flex flex-wrap gap-2 ${item.status === 'SYNCED' && !isEditMode && viewMode !== 'raw' ? '' : 'border-t border-divider'}`}>
                {item.status === 'SYNCED' && !isEditMode && viewMode !== 'raw' ? null : isEditMode ? (
                  <>
                    <Button variant="secondary" onClick={() => { setIsEditMode(false); setEditPayload(null); }} className="flex-1">
                      <X className="w-4 h-4" /> Cancel
                    </Button>
                    <Button variant="primary" onClick={handleRebuildSave} disabled={isSaving} className="flex-1">
                      <Save className="w-4 h-4" /> {isSaving ? 'Saving…' : 'Save & rebuild'}
                    </Button>
                  </>
                ) : viewMode === 'raw' ? (
                  <>
                    <Button variant="secondary" onClick={() => setViewMode('preview')} className="flex-1">
                      <X className="w-4 h-4" /> Cancel
                    </Button>
                    <Button variant="primary" onClick={handleSaveItem} disabled={isSaving} className="flex-1">
                      <Save className="w-4 h-4" /> {isSaving ? 'Saving…' : 'Save changes'}
                    </Button>
                  </>
                ) : isDeliveryUncertain ? (
                  (item.status === 'FAILED' || item.status === 'PENDING') && (
                    <Button variant="secondary" onClick={handleConfirmNotDelivered} className="flex-1">
                      <AlertTriangle className="w-4 h-4" /> Verify in Tally first
                    </Button>
                  )
                ) : (
                  <>
                    {(item.status === 'FAILED' || item.status === 'PENDING') && (
                      <Button variant="secondary" onClick={handleDeleteItem} className="flex-1">
                        <Trash2 className="w-4 h-4" /> Delete
                      </Button>
                    )}

                    {item.status === 'PENDING' && isRebuildable && (
                      <Button variant="secondary" onClick={handleStartEdit} className="flex-1">
                        <Pencil className="w-4 h-4" /> Edit
                      </Button>
                    )}

                    {!isRebuildable && (item.status === 'PENDING' || item.status === 'FAILED') && (
                      <Button variant="secondary" onClick={() => setViewMode('raw')} className="flex-1">
                        <Pencil className="w-4 h-4" /> Edit
                      </Button>
                    )}

                    {item.status === 'FAILED' && (
                      <Button variant="primary" onClick={handleRetryItem} className="flex-1">
                        <RefreshCcw className="w-4 h-4" /> Retry
                      </Button>
                    )}
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
};
