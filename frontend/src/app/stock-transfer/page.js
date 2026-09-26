'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { MasterAutocomplete } from '@/components/ui/MasterAutocomplete';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { useRouter } from 'next/navigation';
import { Package, Store as StoreIcon } from 'lucide-react';

export default function StockTransfer() {
  const router = useRouter();
  const { showToast } = useUI();
  const { user } = useAuth();
  // Unlike the single-store forms, this isn't locked -- a transfer can
  // legitimately go either direction (sending stock out or receiving it
  // in), so a staff account just gets their store pre-filled as the
  // likely "from" side, still free to change either field. The backend
  // still rejects any transfer that doesn't involve their store at all.
  const lockedStore = user && !user.is_owner ? user.store_name : null;

  const [formData, setFormData] = useState({
    item_name: "",
    qty: "",
    from_store: "",
    to_store: "",
    date: ""
  });
  
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const [meta, setMeta] = useState({ items: [], stores: [] });
  const [itemCache, setItemCache] = useState({});
  const [itemsLoading, setItemsLoading] = useState(true);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormData(prev => ({
      ...prev,
      date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0],
      from_store: prev.from_store || lockedStore || "",
    }));
    
    // --- Stale-while-revalidate from localStorage ---

    // 1. Load from cache instantly (0ms)
    const cachedItems = localStorage.getItem('st_item_cache');
    const cachedMeta = localStorage.getItem('st_meta_cache');
    if (cachedItems) {
      try { setItemCache(JSON.parse(cachedItems)); setItemsLoading(false); } catch {}
    }
    if (cachedMeta) {
      try { setMeta(JSON.parse(cachedMeta)); } catch {}
    }

    // 2. Silently refetch in background to keep cache fresh
    fetch("/api/stock-transfer/metadata")
      .then(res => res.json())
      .then(data => { setMeta(data); localStorage.setItem('st_meta_cache', JSON.stringify(data)); })
      .catch(err => console.error("Failed to load metadata", err));
      
    fetch("/api/stock-transfer/items")
      .then(res => res.json())
      .then(data => {
        setItemCache(data);
        setItemsLoading(false);
        localStorage.setItem('st_item_cache', JSON.stringify(data));
      })
      .catch(err => { console.error("Failed to load item cache", err); setItemsLoading(false); });
  }, []);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handlePreview = async (e) => {
    e.preventDefault();

    if (!formData.item_name) {
      showToast("Select an item", "error");
      return;
    }
    if (!formData.qty || parseFloat(formData.qty) <= 0) {
      showToast("Enter a valid quantity", "error");
      return;
    }
    if (!formData.from_store) {
      showToast("Select the source store", "error");
      return;
    }
    if (!formData.to_store) {
      showToast("Select the destination store", "error");
      return;
    }
    if (formData.from_store.trim().toLowerCase() === formData.to_store.trim().toLowerCase()) {
      showToast("From and To stores must be different", "error");
      return;
    }

    setLoading(true);

    try {
      const response = await fetch("/api/stock-transfer/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          item_name: formData.item_name,
          qty: parseFloat(formData.qty),
          from_store: formData.from_store,
          to_store: formData.to_store,
        }),
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        showToast(errorData.detail || 'Could not fetch rate.', 'error');
        setLoading(false);
        return;
      }
      
      const data = await response.json();
      const unit = itemCache[formData.item_name.toLowerCase()]?.unit || '';
      setPreview({ ...data, unit });
    } catch (error) {
      showToast("Network error while generating preview.", 'error');
    }
    
    setLoading(false);
  };

  const handlePost = async () => {
    setPosting(true);
    try {
      const response = await fetch("/api/stock-transfer/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          item_name: formData.item_name,
          qty: parseFloat(formData.qty),
          rate: preview.rate,
          total_amount: preview.total_amount,
          from_store: preview.from_store,
          to_store: preview.to_store,
          tally_date: formData.date.replace(/-/g, '')
        }),
      });
      
      if (response.ok) {
        const data = await response.json();
        if (data.status === "success") {
          showToast("Stock transfer posted to Tally");
        } else if (data.status === "failed") {
          const problems = [];
          if (data.accounting?.status === "failed") problems.push(`Accounting entry: ${data.accounting.message}`);
          if (data.physical?.status === "failed") problems.push(`Stock movement: ${data.physical.message}`);
          showToast(problems.join(" | ") || "Tally rejected the transfer.", 'error');
        } else {
          showToast("Tally is offline. Saved to queue.");
        }
        router.push('/dashboard');
      } else {
        const errorData = await response.json().catch(() => ({}));
        showToast(errorData.detail || 'Failed to post to Tally.', 'error');
      }
    } catch (error) {
      showToast("Network error while posting.", 'error');
    }
    setPosting(false);
  };

  if (preview) {
    return (
      <div className="min-h-screen bg-bg pb-20">
        <TopBar title="Confirm" />
        <div className="max-w-md mx-auto p-4 space-y-6 mt-4">
          <Card className="flex flex-col gap-4">
            <div>
              <p className="text-xs font-semibold text-text/60 uppercase">Item</p>
              <p className="text-lg font-bold text-text">{preview.item_name}</p>
            </div>
            <div className="flex gap-4">
              <div className="flex-1">
                <p className="text-xs font-semibold text-text/60 uppercase">Quantity</p>
                <p className="text-base font-medium text-text">{preview.qty} {preview.unit}</p>
              </div>
              <div className="flex-1">
                <p className="text-xs font-semibold text-text/60 uppercase">Rate</p>
                <p className="text-base font-medium text-text">₹ {preview.rate}</p>
              </div>
            </div>
            <div className="h-px bg-divider my-2" />
            <div className="flex justify-between items-center bg-surface p-3 rounded-lg border border-divider">
              <div className="flex flex-col">
                <p className="text-[10px] font-bold text-text/40 uppercase">From</p>
                <p className="text-sm font-bold text-text">{preview.from_store}</p>
              </div>
              <svg className="w-5 h-5 text-text/30" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" /></svg>
              <div className="flex flex-col items-end">
                <p className="text-[10px] font-bold text-text/40 uppercase">To</p>
                <p className="text-sm font-bold text-accent-700">{preview.to_store}</p>
              </div>
            </div>
          </Card>
          
          <div className="flex gap-3">
            <Button variant="secondary" onClick={() => setPreview(null)} disabled={posting}>
              Back
            </Button>
            <Button onClick={handlePost} disabled={posting} className="flex-1">
              {posting ? "Sending..." : "Send to Tally"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg pb-20">
      <TopBar title="Move stock" showBack />
      
      <div className="max-w-md mx-auto p-4 mt-4">
        <form onSubmit={handlePreview} className="space-y-6">
          <Input 
            label="Date"
            type="date"
            name="date"
            value={formData.date}
            onChange={handleChange}
            required
          />

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-text/70">Item Name</label>
            <MasterAutocomplete
              value={formData.item_name}
              onChange={(val) => setFormData({ ...formData, item_name: val })}
              placeholder="Search or select item..."
              confirmed={Object.keys(itemCache).map(k => itemCache[k].name)}
              createLabel="item"
              icon={Package}
              inputClassName="w-full flex items-center gap-2.5 min-h-[56px] border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body shadow-sm"
            />
          </div>

          <Input 
            label="Quantity"
            type="number"
            step="0.01"
            name="qty"
            placeholder="e.g. 100"
            value={formData.qty}
            onChange={handleChange}
            required
          />

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-text/70">From Store</label>
            <MasterAutocomplete
              value={formData.from_store}
              onChange={(val) => setFormData({ ...formData, from_store: val })}
              placeholder="Select source..."
              confirmed={meta.stores}
              createLabel="store"
              icon={StoreIcon}
              inputClassName="w-full flex items-center gap-2.5 min-h-[56px] border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body shadow-sm"
            />
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-text/70">To Store</label>
            <MasterAutocomplete
              value={formData.to_store}
              onChange={(val) => setFormData({ ...formData, to_store: val })}
              placeholder="Select destination..."
              confirmed={meta.stores}
              createLabel="store"
              icon={StoreIcon}
              inputClassName="w-full flex items-center gap-2.5 min-h-[56px] border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body shadow-sm"
            />
          </div>

          <Button type="submit" disabled={loading} className="mt-8">
            {loading ? "Calculating..." : "Review Transfer"}
          </Button>
        </form>
      </div>
    </div>
  );
}
