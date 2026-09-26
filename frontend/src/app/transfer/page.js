'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { MasterAutocomplete } from '@/components/ui/MasterAutocomplete';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { useUI } from '@/context/UIContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import { numberToWordsIndian } from '@/utils/numberToWords';
import { useRouter } from 'next/navigation';
import { TextArea } from '@/components/ui/TextArea';
import { Building2, WifiOff } from 'lucide-react';

function ReviewRow({ label, value }) {
  return (
    <div className="flex justify-between gap-4 py-3.5 border-b border-divider text-[15px]">
      <span className="text-text/70 shrink-0">{label}</span>
      <span className="text-right text-text">{value || '—'}</span>
    </div>
  );
}

export default function FundTransfer() {
  const router = useRouter();
  const { showToast } = useUI();
  const { isOnline } = useSyncStatus();

  const [formData, setFormData] = useState({
    amount: "",
    from_account: "",
    to_account: "",
    narration: "",
    date: "" // Local YYYY-MM-DD
  });
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const [accounts, setAccounts] = useState([]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormData(prev => ({
      ...prev,
      date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0]
    }));

    fetch("/api/transfer/metadata")
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
      const response = await fetch("/api/transfer/preview", {
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

      const response = await fetch("/api/transfer/post", {
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
    const amtStr = parseFloat(preview.amount).toLocaleString('en-IN');
    const amtWords = numberToWordsIndian(parseFloat(preview.amount));
    const formattedDate = new Date(formData.date).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });

    return (
      <div className="min-h-screen bg-bg pb-40">
        <TopBar 
          title="Review transfer" 
          showBack 
          onBack={() => setPreview(null)} 
          rightContent={<span className="text-[11px] font-medium text-text/60 bg-text/5 px-2 py-1 rounded border border-divider">Step 2 of 2</span>}
        />
        
        <div className="max-w-md mx-auto px-5 pt-8">
          <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700 font-bold mb-4">Check before saving</div>
          <div className="text-[44px] font-heading font-black text-text leading-none">₹{amtStr}</div>
          <div className="text-[14px] font-body text-text/70 italic mt-2 mb-8">Rupees {amtWords} only</div>
          
          <div className="border-t border-divider" />
          <ReviewRow label="Date" value={formattedDate} />
          <ReviewRow label="From account" value={preview.from_account} />
          <ReviewRow label="To account" value={preview.to_account} />
          <ReviewRow label="Notes" value={preview.narration || "—"} />
        </div>

        <div className="fixed bottom-0 left-0 right-0 bg-surface border-t border-divider pb-safe animate-in slide-in-from-bottom-full z-40">
          {!isOnline && (
            <div className="px-5 py-3 border-b border-divider flex gap-3 text-sm text-text/70 bg-bg">
              <WifiOff className="w-4 h-4 shrink-0 mt-0.5 text-accent-700" />
              <p>You're offline. This entry will be saved on this phone and sent to Tally when you reconnect.</p>
            </div>
          )}
          <div className="p-4 flex flex-col gap-3 max-w-md mx-auto">
            <Button onClick={handlePost} disabled={posting} className="w-full h-[52px]">
              {posting ? "Saving..." : "Confirm & save transfer"}
            </Button>
            <button
              onClick={() => setPreview(null)}
              disabled={posting}
              className="w-full h-12 flex items-center justify-center rounded-md font-heading font-semibold text-[17px] border border-divider text-text hover:bg-text/4 transition-colors"
            >
              Edit
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg pb-20">
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

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-text/70">From account</label>
            <MasterAutocomplete
              value={formData.from_account}
              onChange={(val) => setFormData({ ...formData, from_account: val })}
              placeholder="Select source account..."
              confirmed={accounts}
              createLabel="account"
              icon={Building2}
              inputClassName="w-full flex items-center gap-2.5 min-h-[56px] border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body shadow-sm"
            />
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-text/70">To account</label>
            <MasterAutocomplete
              value={formData.to_account}
              onChange={(val) => setFormData({ ...formData, to_account: val })}
              placeholder="Select destination account..."
              confirmed={accounts}
              createLabel="account"
              icon={Building2}
              inputClassName="w-full flex items-center gap-2.5 min-h-[56px] border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body shadow-sm"
            />
          </div>

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
