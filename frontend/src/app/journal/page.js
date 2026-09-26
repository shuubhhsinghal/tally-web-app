'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { MasterAutocomplete } from '@/components/ui/MasterAutocomplete';
import { useUI } from '@/context/UIContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import { numberToWordsIndian } from '@/utils/numberToWords';
import { useRouter } from 'next/navigation';
import { Wallet, User, Calendar, CloudOff, WifiOff } from 'lucide-react';

function ReviewRow({ label, value }) {
  return (
    <div className="flex justify-between gap-4 py-3.5 border-b border-divider text-[15px]">
      <span className="text-text/70 shrink-0">{label}</span>
      <span className="text-right text-text">{value || '—'}</span>
    </div>
  );
}

export default function JournalVoucher() {
  const router = useRouter();
  const { showToast } = useUI();
  const { isOnline } = useSyncStatus();

  const [formData, setFormData] = useState({
    debit_ledger: "",
    credit_ledger: "",
    amount: "",
    date: "",
    narration: ""
  });

  const [preview, setPreview] = useState(null);
  const [posting, setPosting] = useState(false);
  const [successData, setSuccessData] = useState(null);
  const [meta, setMeta] = useState({ ledgers: [], groups: [] });

  const [showCreateAccount, setShowCreateAccount] = useState(false);
  const [newAccount, setNewAccount] = useState({ name: "", group: "" });
  const [creatingAccount, setCreatingAccount] = useState(false);

  const fetchMetadata = () => {
    fetch("/api/journal/metadata")
      .then(res => res.json())
      .then(data => setMeta(prev => ({
        ...prev,
        ledgers: data.ledgers || [],
        groups: data.groups || [],
      })))
      .catch(err => console.error("Failed to load metadata", err));
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormData(prev => ({
      ...prev,
      date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0],
    }));
    fetchMetadata();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleCreateAccount = async () => {
    if (!newAccount.name || !newAccount.group) {
      showToast("Name and Group are required", "error");
      return;
    }
    setCreatingAccount(true);
    try {
      const res = await fetch("/api/journal/create-ledger", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newAccount.name, parent: newAccount.group }),
      });
      const data = await res.json();

      if (res.ok) {
        showToast(data.message);
        setShowCreateAccount(false);
        setNewAccount({ name: "", group: "" });
        // No fixed "which side is new" here -- default it onto the credit
        // side, since a freshly created ledger is far more often something
        // value is coming from than a bank/cash account.
        setFormData(prev => ({ ...prev, credit_ledger: data.name }));
        fetchMetadata();
      } else {
        showToast(data.detail || "Failed to create account", "error");
      }
    } catch (err) {
      showToast("Network error creating account", "error");
    }
    setCreatingAccount(false);
  };

  const handleReview = (e) => {
    e.preventDefault();

    if (!formData.debit_ledger || !formData.credit_ledger) {
      showToast("Select both accounts", "error");
      return;
    }
    if (!formData.amount || parseFloat(formData.amount) <= 0) {
      showToast("Enter an amount", "error");
      return;
    }

    for (const field of ["debit_ledger", "credit_ledger"]) {
      const selected = meta.ledgers.find(l => l.name === formData[field]);
      if (selected && selected.is_pending) {
        showToast("Account is waiting to sync with Tally.", "error");
        return;
      }
    }

    setPreview({ ...formData, amount: parseFloat(formData.amount) });
  };

  const handlePost = async () => {
    setPosting(true);

    try {
      const finalNarration = preview.narration.trim() !== ""
        ? preview.narration
        : `Journal: ₹${preview.amount} from ${preview.credit_ledger} to ${preview.debit_ledger}`;

      const payload = {
        debit_ledger: preview.debit_ledger,
        credit_ledger: preview.credit_ledger,
        amount: preview.amount,
        tally_date: preview.date.replace(/-/g, ''),
        narration: finalNarration,
      };

      const response = await fetch("/api/journal/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await response.json();

      if (response.ok) {
        showToast(data.message || "Entry saved");
        setSuccessData({
          amount: preview.amount,
          debit_ledger: preview.debit_ledger,
          credit_ledger: preview.credit_ledger,
          date: preview.date,
        });
        setPreview(null);
      } else {
        showToast(data.detail || "Failed to post to Tally", "error");
      }
    } catch (error) {
      showToast("Network error while posting", "error");
    }
    setPosting(false);
  };

  const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0];
  const yesterdayDate = new Date(Date.now() - 86400000 - new Date().getTimezoneOffset() * 60000);
  const yesterday = yesterdayDate.toISOString().split('T')[0];

  const getFormattedDate = (dateStr) => {
    if (!dateStr) return "";
    const options = { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' };
    return new Date(dateStr).toLocaleDateString('en-GB', options);
  };

  const getShortDate = (dateStr) => {
    if (!dateStr) return "";
    const options = { weekday: 'short', day: 'numeric', month: 'short' };
    return new Date(dateStr).toLocaleDateString('en-GB', options);
  };

  if (preview) {
    const amtStr = preview.amount.toLocaleString('en-IN');
    const amtWords = numberToWordsIndian(preview.amount);

    return (
      <div className="min-h-screen bg-bg pb-40 font-body">
        <TopBar
          title="Review journal entry"
          showBack
          onBack={() => setPreview(null)}
          rightContent={<span className="text-[11px] font-medium text-text/60 bg-text/5 px-2 py-1 rounded border border-divider">Step 2 of 2</span>}
        />

        <div className="max-w-xl mx-auto px-5 pt-8">
          <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700 font-bold mb-4">Check before saving</div>
          <div className="text-[44px] font-heading font-black text-text leading-none">₹{amtStr}</div>
          <div className="text-[14px] font-body text-text/70 italic mt-2 mb-8">Rupees {amtWords} only</div>

          <div className="border-t border-divider" />
          <ReviewRow label="Date" value={getFormattedDate(preview.date)} />
          <ReviewRow label="Credit account (from)" value={preview.credit_ledger} />
          <ReviewRow label="Debit account (to)" value={preview.debit_ledger} />
          <ReviewRow label="Notes" value={preview.narration} />
        </div>

        {/* z-50: the bottom nav's floating "+" button is also z-40 and sits
            at this same spot -- without outranking it, its hit-area
            silently swallows clicks meant for Confirm/Edit here. */}
        <div className="fixed bottom-0 left-0 right-0 bg-surface border-t border-divider pb-safe animate-in slide-in-from-bottom-full z-50">
          {isOnline === false && (
            <div className="px-5 py-3 border-b border-divider flex gap-3 text-sm text-text/70 bg-bg">
              <WifiOff className="w-4 h-4 shrink-0 mt-0.5 text-accent-700" />
              <p>You&apos;re offline. This entry will be saved on this phone and sent to Tally when you reconnect.</p>
            </div>
          )}
          <div className="p-4 flex flex-col gap-3 max-w-xl mx-auto">
            <Button onClick={handlePost} disabled={posting} className="w-full h-[52px]">
              {posting ? "Saving..." : "Confirm & save journal entry"}
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

  if (successData) {
    return (
      <div className="min-h-screen bg-bg flex flex-col font-body pb-6">
        <div className="w-full max-w-xl mx-auto px-6 pt-16 pb-8 flex-1 flex flex-col">
          <div className="flex flex-col items-center mb-10">
            <div className="w-16 h-16 rounded-full border border-accent flex items-center justify-center mb-4">
              <CloudOff className="w-8 h-8 text-accent-700" />
            </div>
            <span className="text-[11px] font-bold text-accent-700 tracking-widest mb-2 uppercase">Saved</span>
            <h1 className="text-[32px] font-heading font-semibold text-text mb-2">Journal entry saved</h1>
            <p className="text-[15px] text-neutral-700 text-center max-w-[280px]">
              It will sync to Tally automatically.
            </p>
          </div>

          <div className="w-full bg-bg border-t border-divider">
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Amount</span>
              <span className="text-text font-semibold text-[15px]">₹{successData.amount}</span>
            </div>
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Debit</span>
              <span className="text-text font-medium text-[15px] text-right">{successData.debit_ledger}</span>
            </div>
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Date</span>
              <span className="text-text font-medium text-[15px] text-right">{getShortDate(successData.date)}</span>
            </div>
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Credit</span>
              <span className="text-text font-medium text-[15px] text-right">{successData.credit_ledger}</span>
            </div>
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Voucher</span>
              <span className="text-text font-medium text-[15px] text-right">Journal</span>
            </div>
          </div>
        </div>

        <div className="w-full max-w-xl mx-auto px-4 space-y-3 bg-bg border-t border-divider pt-6">
          <Button
            onClick={() => {
              setSuccessData(null);
              setFormData(prev => ({ ...prev, debit_ledger: "", credit_ledger: "", amount: "", narration: "" }));
            }}
          >
            Record another entry
          </Button>
          <Button variant="secondary" onClick={() => router.push('/dashboard')}>
            Done
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg pb-20 font-body">
      <TopBar
        title="Record a journal entry"
        showBack
        rightContent={
          <div className="text-[11px] px-2.5 py-1 border border-divider rounded text-neutral-700 shrink-0">
            Journal voucher
          </div>
        }
      />

      <div className="p-4 mt-2 space-y-6 max-w-xl mx-auto">
        <p className="text-[12.5px] text-neutral-700">
          Move value between any two accounts — a loan repayment, a write-off, moving cash in or out, adjusting a balance.
        </p>

        <form onSubmit={handleReview} className="space-y-7">

          {/* Date Picker Section */}
          <div className="space-y-2">
            <label className="text-[13px] font-medium text-neutral-600">Date</label>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setFormData({ ...formData, date: today })}
                className={`px-4 py-2 text-[14px] font-medium rounded-full border transition-colors ${
                  formData.date === today ? "bg-accent/8 border-accent text-accent-700" : "bg-bg border-divider text-neutral-700"
                }`}
              >
                Today
              </button>
              <button
                type="button"
                onClick={() => setFormData({ ...formData, date: yesterday })}
                className={`px-4 py-2 text-[14px] font-medium rounded-full border transition-colors ${
                  formData.date === yesterday ? "bg-accent/8 border-accent text-accent-700" : "bg-bg border-divider text-neutral-700"
                }`}
              >
                Yesterday
              </button>

              <div className={`relative flex items-center px-4 py-2 rounded-full border ${
                (formData.date !== today && formData.date !== yesterday) ? "bg-accent/8 border-accent text-accent-700" : "bg-bg border-divider text-neutral-700"
              }`}>
                <Calendar className="w-4 h-4 mr-2" />
                <input
                  type="date"
                  name="date"
                  value={formData.date}
                  onChange={handleChange}
                  className="bg-transparent outline-none w-28 text-[14px] font-medium cursor-pointer"
                  required
                />
              </div>
            </div>
            <p className="text-[12px] text-neutral-600 font-medium pl-1 mt-1">
              {getFormattedDate(formData.date)}
            </p>
          </div>

          {/* Amount Section */}
          <div className="space-y-2">
            <label className="text-[13px] font-medium text-neutral-600">Amount</label>
            <div className="relative border-b border-divider pb-2 flex items-center">
              <span className="text-[32px] text-neutral-500 mr-2 leading-none">₹</span>
              <input
                type="number"
                step="0.01"
                name="amount"
                placeholder="0"
                value={formData.amount}
                onChange={handleChange}
                className="w-full bg-transparent outline-none text-[32px] font-heading text-text placeholder:text-neutral-400"
                required
              />
            </div>
          </div>

          {/* Credit account: where the value is coming from */}
          <div className="space-y-1.5">
            <label className="text-[13px] font-body text-neutral-600 font-medium">Credit account</label>
            <p className="text-[12px] text-neutral-600 -mt-1">Where the value is coming from</p>
            <MasterAutocomplete
              value={formData.credit_ledger}
              onChange={val => setFormData({ ...formData, credit_ledger: val })}
              placeholder="Search ledger"
              confirmed={meta.ledgers.map(l => l.name)}
              createLabel="account"
              icon={User}
              inputClassName="bg-transparent border-divider text-[15px]"
            />
          </div>

          {!showCreateAccount && (
            <button
              type="button"
              onClick={() => setShowCreateAccount(true)}
              className="text-sm text-accent-700 font-semibold flex items-center gap-1 ml-1 -mt-3"
            >
              <span>+</span> Create new account
            </button>
          )}

          {showCreateAccount && (
            <div className="bg-accent/8 p-4 rounded border border-accent/30 space-y-4">
              <h4 className="text-sm font-bold text-accent-700">Create Account</h4>
              <Input
                label="Account Name"
                placeholder="e.g. Loan - Ramesh"
                value={newAccount.name}
                onChange={(e) => setNewAccount({...newAccount, name: e.target.value})}
              />
              <Select
                label="Group"
                value={newAccount.group}
                onChange={(e) => setNewAccount({...newAccount, group: e.target.value})}
              >
                <option value="">Select group...</option>
                {meta.groups.map(g => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </Select>
              <div className="flex gap-2">
                <Button type="button" variant="secondary" onClick={() => setShowCreateAccount(false)} className="flex-1">
                  Cancel
                </Button>
                <Button type="button" onClick={handleCreateAccount} disabled={creatingAccount} className="flex-1">
                  {creatingAccount ? '...' : 'Create Account'}
                </Button>
              </div>
            </div>
          )}

          {/* Debit account: where the value is going to */}
          <div className="space-y-1.5">
            <label className="text-[13px] font-body text-neutral-600 font-medium">Debit account</label>
            <p className="text-[12px] text-neutral-600 -mt-1">Where the value is going to</p>
            <MasterAutocomplete
              value={formData.debit_ledger}
              onChange={val => setFormData({ ...formData, debit_ledger: val })}
              placeholder="Search ledger"
              confirmed={meta.ledgers.map(l => l.name)}
              createLabel="account"
              icon={Wallet}
              inputClassName="bg-transparent border-divider text-[15px]"
            />
          </div>

          {/* Notes */}
          <div className="space-y-1.5 pt-2">
            <label className="text-[13px] font-body text-neutral-600 font-medium">Notes (optional)</label>
            <textarea
              name="narration"
              placeholder=""
              value={formData.narration}
              onChange={handleChange}
              className="w-full bg-transparent border border-divider rounded px-4 py-3 outline-none focus:border-accent text-[15px] text-text"
              rows={2}
            />
          </div>

          <div className="pt-6">
            <Button type="submit">
              Review
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
