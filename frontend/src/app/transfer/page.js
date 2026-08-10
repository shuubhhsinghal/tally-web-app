'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { useUI } from '@/context/UIContext';
import { useRouter } from 'next/navigation';
import { TextArea } from '@/components/ui/TextArea';

export default function FundTransfer() {
  const router = useRouter();
  const { showToast } = useUI();

  const [formData, setFormData] = useState({
    amount: "",
    from_account: "",
    to_account: "",
    narration: "",
    date: new Date().toISOString().split('T')[0] // YYYY-MM-DD
  });
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const [accounts, setAccounts] = useState([]);

  useEffect(() => {
    fetch("http://127.0.0.1:8000/api/transfer/metadata")
      .then(res => res.json())
      .then(data => setAccounts(data.accounts || []))
      .catch(err => console.error("Failed to load accounts", err));
  }, []);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handlePreview = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const response = await fetch("http://127.0.0.1:8000/api/transfer/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount: parseFloat(formData.amount),
          from_account: formData.from_account,
          to_account: formData.to_account,
          tally_date: formData.date.replace(/-/g, ''),
          narration: formData.narration,
        }),
      });
      if (!response.ok) {
        showToast("Error fetching preview", "error");
        setLoading(false);
        return;
      }
      const data = await response.json();
      setPreview(data);
    } catch (error) {
      showToast("Error generating preview", "error");
    }
    setLoading(false);
  };

  const handlePost = async () => {
    setPosting(true);
    try {
      const finalNarration = preview.narration.trim() !== ""
        ? preview.narration
        : `Transferred INR ${preview.amount} from ${preview.from_account} to ${preview.to_account}`;

      const response = await fetch("http://127.0.0.1:8000/api/transfer/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount: preview.amount,
          from_account: preview.from_account,
          to_account: preview.to_account,
          tally_date: preview.tally_date,
          narration: finalNarration,
        }),
      });

      if (response.ok) {
        showToast("Transfer saved");
        router.push('/dashboard');
      } else {
        showToast("Failed to post to Tally.", "error");
      }
    } catch (error) {
      showToast("Network error while posting.", "error");
    }
    setPosting(false);
  };

  if (preview) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
        <TopBar title="Confirm Transfer" />
        <div className="max-w-md mx-auto p-4 space-y-6 mt-4">
          <Card className="flex flex-col gap-4">
            <div className="text-center">
              <p className="text-xs font-semibold text-gray-500 uppercase">Amount</p>
              <p className="text-3xl font-black text-gray-900 dark:text-white">₹ {preview.amount}</p>
            </div>
            <div className="h-px bg-gray-100 dark:bg-gray-800 my-2" />
            <div className="flex justify-between items-center bg-gray-50 dark:bg-gray-800 p-3 rounded-lg border border-gray-100 dark:border-gray-700">
              <div className="flex flex-col flex-1 truncate">
                <p className="text-[10px] font-bold text-gray-400 uppercase">From</p>
                <p className="text-sm font-bold text-red-600 truncate pr-2">{preview.from_account}</p>
              </div>
              <svg className="w-5 h-5 text-gray-300 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" /></svg>
              <div className="flex flex-col items-end flex-1 truncate">
                <p className="text-[10px] font-bold text-gray-400 uppercase">To</p>
                <p className="text-sm font-bold text-green-600 truncate pl-2">{preview.to_account}</p>
              </div>
            </div>
            <div className="mt-2">
              <p className="text-xs font-semibold text-gray-500 uppercase">Notes</p>
              <p className="text-sm font-medium text-gray-900 dark:text-white mt-1">
                {preview.narration || `Transferred INR ${preview.amount} from ${preview.from_account} to ${preview.to_account}`}
              </p>
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
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar title="Move money" showBack />
      
      <div className="max-w-md mx-auto p-4 mt-4">
        <form onSubmit={handlePreview} className="space-y-6">
          <div className="flex gap-4">
            <Input 
              label="Date"
              type="date"
              name="date"
              value={formData.date}
              onChange={handleChange}
              required
              className="flex-1"
            />
          </div>

          <Input 
            label="Amount"
            type="number"
            step="0.01"
            name="amount"
            placeholder="e.g. 5000"
            value={formData.amount}
            onChange={handleChange}
            required
          />

          <Select 
            label="From account"
            name="from_account"
            value={formData.from_account}
            onChange={handleChange}
            required
          >
            <option value="">Select source account...</option>
            {accounts.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </Select>

          <Select 
            label="To account"
            name="to_account"
            value={formData.to_account}
            onChange={handleChange}
            required
          >
            <option value="">Select destination account...</option>
            {accounts.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </Select>

          <TextArea 
            label="Notes (Optional)"
            name="narration"
            placeholder="What was this transfer for?"
            value={formData.narration}
            onChange={handleChange}
          />

          <Button type="submit" disabled={loading} className="mt-8">
            {loading ? "Calculating..." : "Review Transfer"}
          </Button>
        </form>
      </div>
    </div>
  );
}
