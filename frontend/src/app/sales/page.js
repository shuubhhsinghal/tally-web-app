'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { TextArea } from '@/components/ui/TextArea';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { useRouter } from 'next/navigation';
import { SALES_LEDGERS } from '@/utils/salesLedgers';

export default function SalesVoucher() {
  const router = useRouter();
  const { showToast } = useUI();
  const { user } = useAuth();
  const lockedStore = user && !user.is_owner ? user.store_name : null;

  const [formData, setFormData] = useState({
    ledger: "",
    amount: "",
    narration: "",
    store: "",
    date: "" // Local YYYY-MM-DD
  });

  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);

  const [meta, setMeta] = useState({ customers: [], stores: ["Mahagun", "Vvip", "Gulshan"] });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormData(prev => ({
      ...prev,
      date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0],
      store: lockedStore || prev.store,
    }));

    fetch("/api/sales/metadata")
      .then(res => res.json())
      .then(data => setMeta(data))
      .catch(err => console.error("Failed to load metadata", err));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedStore]);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handlePreview = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const response = await fetch("/api/sales/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ledger: formData.ledger,
          amount: parseFloat(formData.amount),
          tally_date: formData.date.replace(/-/g, ''),
          narration: formData.narration,
          store: formData.store,
        }),
      });
      if (!response.ok) {
        showToast("Error generating preview", "error");
        setLoading(false);
        return;
      }
      const data = await response.json();
      setPreview(data);
    } catch (error) {
      showToast("Network error generating preview", "error");
    }
    setLoading(false);
  };

  const handlePost = async () => {
    setPosting(true);
    try {
      const finalNarration = formData.narration.trim() !== "" 
        ? formData.narration 
        : `Recorded sales of ₹${formData.amount} for ${formData.ledger}`;

      const response = await fetch("/api/sales/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ledger: preview.ledger,
          amount: preview.amount,
          tally_date: preview.tally_date,
          narration: finalNarration,
          store: preview.store,
        }),
      });
      
      if (response.ok) {
        showToast("Sale saved");
        router.push('/dashboard');
      } else {
        showToast("Failed to post to Tally", "error");
      }
    } catch (error) {
      showToast("Network error while posting", "error");
    }
    setPosting(false);
  };

  if (preview) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
        <TopBar title="Confirm Sale" />
        <div className="max-w-md mx-auto p-4 space-y-6 mt-4">
          <Card className="flex flex-col gap-4 text-center">
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase">Amount</p>
              <p className="text-4xl font-black text-gray-900 dark:text-white mt-1">₹ {preview.amount}</p>
            </div>
            <div className="h-px bg-gray-100 dark:bg-gray-800 my-2" />
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase">Ledger</p>
              <p className="text-xl font-bold text-gray-900 dark:text-white mt-1">{preview.ledger}</p>
            </div>
            <div className="h-px bg-gray-100 dark:bg-gray-800 my-2" />
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase">Store</p>
              <p className="text-xl font-bold text-gray-900 dark:text-white mt-1">{preview.store}</p>
            </div>
            {formData.narration && (
              <>
                <div className="h-px bg-gray-100 dark:bg-gray-800 my-2" />
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase">Notes</p>
                  <p className="text-base font-medium text-gray-900 dark:text-white mt-1">{formData.narration}</p>
                </div>
              </>
            )}
          </Card>
          
          <div className="flex flex-col gap-3">
            <Button onClick={handlePost} disabled={posting} className="w-full text-base py-3 font-bold">
              {posting ? "Sending..." : "Send to Tally"}
            </Button>
            <Button variant="secondary" onClick={() => setPreview(null)} disabled={posting} className="w-full text-base py-3 font-bold">
              Back
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar title="Record a sale" showBack />
      
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

          <Input 
            label="Total Amount"
            type="number"
            step="0.01"
            name="amount"
            placeholder="e.g. 25000"
            value={formData.amount}
            onChange={handleChange}
            required
          />

          <Select
            label="Customer / Ledger"
            name="ledger"
            value={formData.ledger}
            onChange={handleChange}
            required
          >
            <option value="">Select ledger...</option>
            {SALES_LEDGERS.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </Select>

          <Select
            label="Store"
            name="store"
            value={formData.store}
            onChange={handleChange}
            required
            disabled={!!lockedStore}
          >
            <option value="" disabled>Select store...</option>
            {meta.stores.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </Select>

          <TextArea
            label="Notes (Optional)"
            name="narration"
            placeholder="Any extra details?"
            value={formData.narration}
            onChange={handleChange}
          />

          <Button type="submit" disabled={loading} className="mt-8">
            {loading ? "Calculating..." : "Review Sale"}
          </Button>
        </form>
      </div>
    </div>
  );
}
