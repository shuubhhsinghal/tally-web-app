'use client';
import { useState, useEffect } from "react";
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { MasterAutocomplete } from '@/components/ui/MasterAutocomplete';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { History, Trash2, X, User } from 'lucide-react';

export function PurchaseReturnMode({ onPostSuccess }) {
  const { showToast } = useUI();
  const { user } = useAuth();
  const lockedStore = user && !user.is_owner ? user.store_name : null;

  const [meta, setMeta] = useState({ suppliers: [], stock_items: [], stock_item_units: {}, stores: [] });
  const [supplier, setSupplier] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [date, setDate] = useState("");
  const [store, setStore] = useState(lockedStore || "");
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const [items, setItems] = useState([]); // [{ name, uom, qty, rate, rateStatus: 'idle'|'loading'|'ok'|'error', rateError }]
  const [posting, setPosting] = useState(false);
  const [successData, setSuccessData] = useState(null);
  const [creatingSupplier, setCreatingSupplier] = useState(false);
  const [historyPicker, setHistoryPicker] = useState({ open: false, itemIdx: null, itemName: null, loading: false, error: null, source: null, entries: [] });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDate(new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0]);
    fetch("/api/purchase-item/metadata")
      .then(res => res.json())
      .then(data => setMeta(data))
      .catch(err => console.error("Failed to load metadata", err));
  }, []);

  useEffect(() => {
    if (lockedStore) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStore(lockedStore);
    }
  }, [lockedStore]);

  const refreshRate = async (name, qty) => {
    try {
      const res = await fetch('/api/purchase-item/return-item-rate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: name, qty })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'No rate found for this item.');
      setItems(prev => prev.map(i => i.name === name
        ? { ...i, rate: data.rate, rateStatus: 'ok', rateError: null }
        : i));
    } catch (err) {
      setItems(prev => prev.map(i => i.name === name
        ? { ...i, rate: 0, rateStatus: 'error', rateError: err.message }
        : i));
    }
  };

  const openHistoryPicker = async (idx, itemName) => {
    setHistoryPicker({ open: true, itemIdx: idx, itemName, loading: true, error: null, source: null, entries: [] });
    try {
      const url = '/api/purchase-item/return-item-history?' + new URLSearchParams({ item_name: itemName });
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to fetch purchase history.');
      setHistoryPicker(prev => ({ ...prev, loading: false, source: data.source, entries: data.entries || [] }));
    } catch (err) {
      setHistoryPicker(prev => ({ ...prev, loading: false, error: err.message }));
    }
  };

  const closeHistoryPicker = () => setHistoryPicker({ open: false, itemIdx: null, itemName: null, loading: false, error: null, source: null, entries: [] });

  const handleSelectHistoryRate = (entry) => {
    setItems(prev => prev.map((i, idx) => idx === historyPicker.itemIdx
      ? { ...i, rate: entry.rate, rateStatus: 'ok', rateError: null }
      : i));
    closeHistoryPicker();
  };

  const handleAddItem = (name) => {
    if (!name || items.some(i => i.name === name)) return;
    const uom = meta.stock_item_units?.[name] || 'PCS';
    const newRow = { name, uom, qty: 1, rate: 0, rateStatus: 'loading', rateError: null };
    const newIdx = items.length;
    setItems(prev => [...prev, newRow]);
    // Fetch a sensible default rate in the background, and open the 2-year
    // purchase-history picker immediately too -- same pattern as the nested
    // Supplier Adjustment/Return inside Item-wise Purchase.
    refreshRate(name, 1);
    openHistoryPicker(newIdx, name);
  };

  const handleQtyChange = (idx, qty) => {
    setItems(prev => prev.map((i, iidx) => iidx === idx ? { ...i, qty } : i));
  };

  const handleRateChange = (idx, rate) => {
    // Typing a valid rate clears whatever auto-lookup state was showing
    // (loading/error) -- a manual entry always wins over the fetched one.
    setItems(prev => prev.map((i, iidx) => iidx === idx
      ? { ...i, rate, rateStatus: (rate !== '' && Number(rate) > 0) ? 'ok' : i.rateStatus, rateError: null }
      : i));
  };

  const handleRemoveItem = (idx) => {
    setItems(prev => prev.filter((_, iidx) => iidx !== idx));
  };

  const handleCreateSupplier = async (supplierName) => {
    setCreatingSupplier(true);
    try {
      const res = await fetch("/api/purchase-item/create-supplier", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: supplierName })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to create supplier");
      setSupplier(supplierName);
      fetch("/api/purchase-item/metadata")
        .then(r => r.json())
        .then(d => setMeta(prev => ({ ...prev, ...d })))
        .catch(err => console.error(err));
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setCreatingSupplier(false);
    }
  };

  const returnTotal = items.reduce((sum, i) => sum + (Number(i.qty) || 0) * (Number(i.rate) || 0), 0);

  const handlePost = async () => {
    if (!supplier) {
      showToast("Select the supplier", "error");
      return;
    }
    if (!invoiceNumber.trim()) {
      showToast("Enter the original invoice number this return is against", "error");
      return;
    }
    if (!store) {
      showToast("Select a store", "error");
      return;
    }
    if (items.length === 0) {
      showToast("Add at least one item to return", "error");
      return;
    }
    if (items.some(i => i.rateStatus === 'error' || !(Number(i.rate) > 0))) {
      showToast("Resolve a rate for every return item first", "error");
      return;
    }

    setPosting(true);
    try {
      const payload = {
        supplier,
        invoice_number: invoiceNumber,
        tally_date: date.replace(/-/g, ''),
        adjustment: {
          store,
          reason: reason || "",
          notes: notes || "",
          items: items.map(i => ({
            name: i.name,
            uom: i.uom,
            qty: Number(i.qty),
            rate: Number(i.rate),
            amount: Number(i.qty) * Number(i.rate),
          })),
        },
      };

      const res = await fetch("/api/purchase-item/return", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (res.ok) {
        showToast(data.message || "Return saved");
        setSuccessData({
          amount: data.return_total ?? returnTotal,
          supplier,
          invoiceNumber,
          date,
          itemCount: items.length,
        });
      } else {
        showToast(data.detail || "Failed to post return", "error");
      }
    } catch (err) {
      showToast("Network error while posting", "error");
    }
    setPosting(false);
  };

  if (successData) {
    return (
      <div className="flex flex-col items-center text-center py-10 gap-2">
        <h2 className="text-2xl font-heading font-semibold text-text">Return saved</h2>
        <p className="text-neutral-700 text-[15px]">
          ₹{Number(successData.amount).toFixed(2)} against {successData.supplier} (Inv. {successData.invoiceNumber}), {successData.itemCount} item{successData.itemCount === 1 ? '' : 's'}.
        </p>
        <div className="w-full max-w-xs space-y-3 mt-6">
          <Button
            onClick={() => {
              setSuccessData(null);
              setItems([]);
              setInvoiceNumber("");
              setReason("");
              setNotes("");
            }}
          >
            Record another return
          </Button>
          <Button variant="secondary" onClick={() => onPostSuccess && onPostSuccess()}>
            Done
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-neutral-700 text-[15px] leading-relaxed">
        Record a Supplier Return (Debit Note) against a past invoice, on its own -- separate from entering a new purchase.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label className="text-[13px] font-body text-neutral-600 font-medium">Return date</label>
          <input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="w-full p-2.5 text-[15px] font-body text-text bg-surface border border-divider rounded outline-none focus:border-accent transition-colors"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-[13px] font-body text-neutral-600 font-medium">Original invoice no.</label>
          <input
            type="text"
            placeholder="e.g. SH/2245"
            value={invoiceNumber}
            onChange={e => setInvoiceNumber(e.target.value)}
            className="w-full p-2.5 text-[15px] font-body text-text bg-surface border border-divider rounded outline-none focus:border-accent transition-colors placeholder:text-neutral-500"
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <label className="text-[13px] font-body text-neutral-600 font-medium">Supplier</label>
        <MasterAutocomplete
          value={supplier}
          onChange={setSupplier}
          placeholder="Select or type supplier"
          confirmed={meta.suppliers}
          masterStates={meta.master_states?.ledgers}
          onCreate={handleCreateSupplier}
          isCreating={creatingSupplier}
          createLabel="supplier"
          icon={User}
          inputClassName="bg-transparent border-divider text-[15px]"
        />
      </div>

      <Card className="flex flex-col gap-3">
        <span className="font-bold text-text">Return items</span>
        <div className="space-y-1.5">
          <label className="text-[10px] font-bold text-neutral-600 uppercase tracking-wider">Add Return Item</label>
          <SearchableSelect
            options={meta.stock_items.filter(n => !items.some(i => i.name === n))}
            value=""
            onChange={handleAddItem}
            placeholder="Search item to add to return..."
          />
        </div>

        {items.length > 0 && (
          <div className="space-y-2">
            {items.map((ri, idx) => (
              <div key={ri.name} className="flex flex-col gap-2 bg-surface p-2 rounded-lg border border-divider">
                <div className="flex items-center gap-2">
                  <span className="flex-1 text-sm font-medium text-text break-words" title={ri.name}>{ri.name}</span>
                  <button type="button" onClick={() => openHistoryPicker(idx, ri.name)} title="Pick a different rate from purchase history" className="text-accent-700 hover:text-accent-700 shrink-0">
                    <History className="w-4 h-4" />
                  </button>
                  <button type="button" onClick={() => handleRemoveItem(idx)} className="text-accent-700 hover:text-accent-800 shrink-0">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="number" step="0.01" min="0" value={ri.qty}
                    onChange={e => handleQtyChange(idx, e.target.value)}
                    className="w-16 p-2 text-sm rounded border border-divider bg-bg outline-none focus:border-accent"
                  />
                  <span className="text-[10px] text-neutral-500 w-8">{ri.uom}</span>
                  <div className="flex-1 flex items-center justify-end gap-1">
                    <span className="text-neutral-500 text-xs">₹</span>
                    <input
                      type="number" step="0.01" min="0"
                      value={ri.rate || ''}
                      placeholder={ri.rateStatus === 'loading' ? 'Fetching...' : '0.00'}
                      onChange={e => handleRateChange(idx, e.target.value)}
                      className={`w-20 p-1.5 text-sm text-right rounded border bg-bg outline-none focus:border-accent ${ri.rateStatus === 'error' ? 'border-accent-700' : 'border-divider'}`}
                    />
                    <span className="text-[10px] text-neutral-500">/unit</span>
                  </div>
                  <span className="w-20 text-sm text-right font-bold text-text">
                    ₹{((Number(ri.qty) || 0) * (Number(ri.rate) || 0)).toFixed(2)}
                  </span>
                </div>
                {ri.rateStatus === 'error' && (
                  <p className="text-[11px] text-accent-800 font-medium text-right">No rate found — enter one manually</p>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label className="text-[10px] font-bold text-neutral-600 uppercase tracking-wider">Store <span className="text-accent-800">*</span></label>
          <select
            value={store}
            onChange={e => setStore(e.target.value)}
            disabled={!!lockedStore}
            className={`w-full p-2 text-sm rounded border bg-bg outline-none focus:border-accent ${!store && items.length > 0 ? 'border-accent' : 'border-divider'}`}
          >
            <option value="" disabled>Select Store...</option>
            {meta.stores.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="space-y-1.5">
          <label className="text-[10px] font-bold text-neutral-600 uppercase tracking-wider">Reason (Optional)</label>
          <input type="text" value={reason} onChange={e => setReason(e.target.value)} className="w-full p-2 text-sm rounded border border-divider bg-bg outline-none focus:border-accent" placeholder="e.g. Damaged goods" />
        </div>
      </div>

      <div className="space-y-1.5">
        <label className="text-[10px] font-bold text-neutral-600 uppercase tracking-wider">Notes (Optional)</label>
        <input type="text" value={notes} onChange={e => setNotes(e.target.value)} className="w-full p-2 text-sm rounded border border-divider bg-bg outline-none focus:border-accent" />
      </div>

      {returnTotal > 0 && (
        <div className="flex justify-between items-center bg-surface p-3 rounded-lg border border-divider">
          <span className="font-bold text-text text-sm">Total Return Amount:</span>
          <span className="font-black text-accent-700 text-lg">₹{returnTotal.toFixed(2)}</span>
        </div>
      )}

      <Button onClick={handlePost} disabled={posting}>
        {posting ? "Saving..." : "Post return"}
      </Button>

      {historyPicker.open && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={closeHistoryPicker} />
          <div className="fixed inset-x-0 bottom-0 sm:inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 pointer-events-none">
            <div
              className="bg-surface border-t sm:border border-divider rounded-t-2xl sm:rounded-lg shadow-lg w-full sm:max-w-lg max-h-[80vh] flex flex-col pointer-events-auto animate-in slide-in-from-bottom-full sm:zoom-in-95"
              onClick={e => e.stopPropagation()}
            >
              <div className="w-12 h-1.5 bg-divider rounded-full mx-auto mt-3 sm:hidden" />
              <div className="flex items-center justify-between px-6 py-4 border-b border-divider">
                <div>
                  <h3 className="font-heading font-semibold text-xl">Purchase history</h3>
                  <p className="text-sm text-neutral-700 truncate max-w-[240px]">{historyPicker.itemName}</p>
                </div>
                <button onClick={closeHistoryPicker} className="text-neutral-500 hover:text-text transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="overflow-y-auto flex-1 p-4">
                {historyPicker.loading && (
                  <p className="text-sm text-neutral-600 text-center py-8">Fetching purchase history...</p>
                )}
                {historyPicker.error && (
                  <div className="text-center py-8">
                    <p className="text-sm text-accent-800 mb-2">{historyPicker.error}</p>
                    <button onClick={() => openHistoryPicker(historyPicker.itemIdx, historyPicker.itemName)} className="text-sm text-accent-700 font-semibold">Retry</button>
                  </div>
                )}
                {!historyPicker.loading && !historyPicker.error && (
                  <>
                    {historyPicker.source === 'local_cache' && (
                      <p className="text-xs border border-accent bg-accent/8 text-accent-700 rounded-md p-2 mb-3">
                        Showing last known rates — Tally is offline right now.
                      </p>
                    )}
                    {historyPicker.entries.length === 0 ? (
                      <p className="text-sm text-neutral-600 text-center py-8">No purchase history in the last 2 years for this item.</p>
                    ) : (
                      <div className="flex flex-col gap-2">
                        {historyPicker.entries.map((entry, eidx) => (
                          <button
                            key={eidx}
                            onClick={() => handleSelectHistoryRate(entry)}
                            className="w-full text-left p-3 rounded-md border border-divider hover:border-accent hover:bg-accent/8 transition-colors"
                          >
                            <div className="flex justify-between items-center">
                              <span className="text-[15px] font-medium text-text">{entry.date}</span>
                              <span className="font-heading font-semibold text-lg text-accent-700">₹{entry.rate}</span>
                            </div>
                            <div className="flex justify-between items-center mt-1 text-sm text-neutral-700">
                              <span className="truncate max-w-[150px]">
                                {entry.origin === 'repack' ? 'Made in-house (Repack)' : (entry.supplier || 'Unknown supplier')}
                              </span>
                              <span>{entry.voucher_number || 'No voucher #'} · Qty {entry.qty ?? '—'} {entry.unit || ''}</span>
                            </div>
                            {entry.origin === 'app_post' && (
                              <span className="inline-block mt-1.5 text-[10.5px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 border border-accent px-2 py-0.5 rounded">
                                Pending Tally sync
                              </span>
                            )}
                            {entry.origin === 'repack' && (
                              <span className="inline-block mt-1.5 text-[10.5px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 border border-accent px-2 py-0.5 rounded">
                                Repack cost
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
