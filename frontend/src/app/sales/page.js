'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { TextArea } from '@/components/ui/TextArea';
import { useUI } from '@/context/UIContext';
import { useRouter } from 'next/navigation';

const SALES_LEDGERS = [
  "Cash Mahagun",
  "Cash Vvip",
  "Cash Gulshan",
  "Paytm Mahagun",
  "Paytm Vvip",
  "Paytm Gulshan",
  "Gpay Vvip"
];

export default function SalesVoucher() {
  const router = useRouter();
  const { showToast } = useUI();

  const [formData, setFormData] = useState({
    ledger: "",
    amount: "",
    narration: "",
    date: "" // Local YYYY-MM-DD
  });
  
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  
  const [meta, setMeta] = useState({ customers: [] });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormData(prev => ({
      ...prev,
      date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0]
    }));

    fetch("/api/sales/metadata")
      .then(res => res.json())
      .then(data => setMeta(data))
      .catch(err => console.error("Failed to load metadata", err));
  }, []);

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
          <Card className="flex flex-col gap-4">
            <div className="text-center">
              <p className="text-xs font-semibold text-gray-500 uppercase">Amount</p>
              <p className="text-3xl font-black text-gray-900 dark:text-white">₹ {preview.amount}</p>
            </div>
            <div className="h-px bg-gray-100 dark:bg-gray-800 my-2" />
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase">Ledger</p>
              <p className="text-base font-medium text-gray-900 dark:text-white mt-1">{preview.ledger}</p>
            </div>
            {formData.narration && (
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Notes</p>
                <p className="text-sm font-medium text-gray-900 dark:text-white mt-1">{formData.narration}</p>
              </div>
            )}
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
