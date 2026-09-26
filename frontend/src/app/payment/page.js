'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { TextArea } from '@/components/ui/TextArea';
import { MasterAutocomplete } from '@/components/ui/MasterAutocomplete';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { useRouter, useSearchParams } from 'next/navigation';
import { User, Store, Wallet, Calendar, CloudOff, Cloud } from 'lucide-react';

export default function PaymentVoucher() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { showToast } = useUI();
  const { user } = useAuth();
  const lockedStore = user && !user.is_owner ? user.store_name : null;
  // Deep-linked from the Creditors report's "Record payment" button --
  // pre-selects the supplier so the owner doesn't have to search for them
  // again right after looking them up.
  const payTo = searchParams.get('pay_to') || "";

  const [formData, setFormData] = useState({
    mode: payTo ? "others" : "expenses", // 'expenses' or 'others'
    debit_ledger: payTo || "",
    credit_ledger: "",
    amount: "",
    date: "",
    cost_center: "",
    narration: ""
  });
  
  const [loading, setLoading] = useState(false);
  const [successData, setSuccessData] = useState(null);
  const [meta, setMeta] = useState({ 
    expense_paid_to: [], 
    party_paid_to: [], 
    paid_from: [], 
    groups: [], 
    stores: ["Mahagun", "Vvip", "Gulshan"] 
  });
  
  const [showCreateAccount, setShowCreateAccount] = useState(false);
  const [newAccount, setNewAccount] = useState({ name: "", group: "" });
  const [creatingAccount, setCreatingAccount] = useState(false);

  const fetchMetadata = () => {
    fetch("/api/payment/metadata")
      .then(res => res.json())
      .then(data => setMeta(prev => ({
        ...prev,
        expense_paid_to: data.expense_paid_to || [],
        party_paid_to: data.party_paid_to || [],
        paid_from: data.paid_from || [],
        groups: data.groups || [],
        stores: data.stores || prev.stores
      })))
      .catch(err => console.error("Failed to load metadata", err));
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormData(prev => ({
      ...prev,
      date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0],
      cost_center: (prev.mode === "expenses" && lockedStore) ? lockedStore : prev.cost_center,
    }));
    fetchMetadata();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedStore]);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleModeChange = (mode) => {
    const cost_center = mode === "others" ? "" : (lockedStore || formData.cost_center);
    setFormData({ ...formData, mode, cost_center, debit_ledger: "" });
    setShowCreateAccount(false);
  };

  const handleCreateAccount = async () => {
    if (!newAccount.name || !newAccount.group) {
      showToast("Name and Group are required", "error");
      return;
    }
    setCreatingAccount(true);
    try {
      const res = await fetch("/api/payment/create-ledger", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newAccount.name, parent: newAccount.group }),
      });
      const data = await res.json();
      
      if (res.ok) {
        showToast(data.message);
        setShowCreateAccount(false);
        setNewAccount({ name: "", group: "" });
        // Set debit ledger to the new account
        setFormData(prev => ({ ...prev, debit_ledger: data.name }));
        // Refresh metadata to show the queued account
        fetchMetadata();
      } else {
        showToast(data.detail || "Failed to create account", "error");
      }
    } catch (err) {
      showToast("Network error creating account", "error");
    }
    setCreatingAccount(false);
  };

  const handlePost = async (e) => {
    e.preventDefault();

    if (!formData.amount || parseFloat(formData.amount) <= 0) {
      showToast("Enter the amount", "error");
      return;
    }

    if (!formData.debit_ledger) {
      showToast("Select who this was paid to", "error");
      return;
    }

    if (!formData.credit_ledger) {
      showToast("Select which account this was paid from", "error");
      return;
    }

    // Safety check for pending debit ledger
    const activeList = formData.mode === 'expenses' ? meta.expense_paid_to : meta.party_paid_to;
    const selectedLedger = activeList.find(l => l.name === formData.debit_ledger);
    if (selectedLedger && selectedLedger.is_pending) {
      showToast("Account is waiting to sync with Tally.", "error");
      return;
    }

    setLoading(true);
    
    try {
      const finalNarration = formData.narration.trim() !== "" 
        ? formData.narration 
        : `Paid ₹${formData.amount} to ${formData.debit_ledger} via ${formData.credit_ledger}`;

      const payload = {
        mode: formData.mode,
        debit_ledger: formData.debit_ledger,
        credit_ledger: formData.credit_ledger,
        amount: parseFloat(formData.amount),
        tally_date: formData.date.replace(/-/g, ''),
        narration: finalNarration,
        cost_center: formData.mode === "expenses" ? formData.cost_center : null,
      };

      const response = await fetch("/api/payment/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();

      if (response.ok) {
        showToast(data.message || "Payment saved");

        let queueCount = null;
        if (data.status !== 'success') {
          try {
            const statsRes = await fetch('/api/dashboard/stats');
            if (statsRes.ok) queueCount = (await statsRes.json()).queue_count;
          } catch { }
        }

        setSuccessData({
          amount: parseFloat(formData.amount),
          debit_ledger: formData.debit_ledger,
          credit_ledger: formData.credit_ledger,
          date: formData.date,
          cost_center: formData.mode === "expenses" ? formData.cost_center : null,
          synced: data.status === 'success',
          queueCount
        });
      } else {
        showToast(data.detail || "Failed to post to Tally", "error");
      }
    } catch (error) {
      showToast("Network error while posting", "error");
    }
    setLoading(false);
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

  if (successData) {
    return (
      <div className="min-h-screen bg-bg flex flex-col font-body pb-6">
        <div className="w-full max-w-xl mx-auto px-6 pt-16 pb-8 flex-1 flex flex-col">
          {/* Top Icon & Titles */}
          <div className="flex flex-col items-center mb-10">
            <div className="w-16 h-16 rounded-full border border-accent flex items-center justify-center mb-4">
              {successData.synced ? <Cloud className="w-8 h-8 text-accent-700" /> : <CloudOff className="w-8 h-8 text-accent-700" />}
            </div>
            <span className="text-[11px] font-bold text-accent-700 tracking-widest mb-2 uppercase">{successData.synced ? 'Sent to Tally' : 'Saved Offline'}</span>
            <h1 className="text-[32px] font-heading font-semibold text-text mb-2">{successData.synced ? 'Sent to Tally' : 'Saved on this phone'}</h1>
            <p className="text-[15px] text-neutral-700 text-center max-w-[280px]">
              {successData.synced
                ? 'This payment has been posted to Tally successfully.'
                : `It will go to Tally automatically when you're back online.${successData.queueCount != null ? ` ${successData.queueCount} entr${successData.queueCount === 1 ? 'y is' : 'ies are'} waiting.` : ''}`}
            </p>
          </div>

          {/* Summary Card */}
          <div className="w-full bg-bg border-t border-divider">
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Amount</span>
              <span className="text-text font-semibold text-[15px]">₹{successData.amount}</span>
            </div>
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Paid to</span>
              <span className="text-text font-medium text-[15px] text-right">{successData.debit_ledger}</span>
            </div>
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Date</span>
              <span className="text-text font-medium text-[15px] text-right">{getShortDate(successData.date)}</span>
            </div>
            {successData.cost_center && (
              <div className="flex justify-between py-4 border-b border-divider">
                <span className="text-neutral-600 text-[15px]">Store</span>
                <span className="text-text font-medium text-[15px] text-right">{successData.cost_center}</span>
              </div>
            )}
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Paid from</span>
              <span className="text-text font-medium text-[15px] text-right">{successData.credit_ledger}</span>
            </div>
            <div className="flex justify-between py-4 border-b border-divider">
              <span className="text-neutral-600 text-[15px]">Voucher</span>
              <span className="text-text font-medium text-[15px] text-right">Payment</span>
            </div>
          </div>
        </div>

        {/* Bottom Actions */}
        <div className="w-full max-w-xl mx-auto px-4 space-y-3 bg-bg border-t border-divider pt-6">
          <Button
            onClick={() => {
              setSuccessData(null);
              setFormData(prev => ({
                ...prev,
                debit_ledger: "",
                amount: "",
                narration: ""
              }));
            }}
          >
            Record another payment
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
        title="Record a payment"
        showBack
        rightContent={
          <div className="text-[11px] px-2.5 py-1 border border-divider rounded text-neutral-700 shrink-0">
            Payment voucher
          </div>
        }
      />

      <div className="p-4 mt-2 space-y-6 max-w-xl mx-auto">

        {/* Segmented Control */}
        <div className="grid grid-cols-2 border border-divider rounded-md overflow-hidden w-full">
          <button
            type="button"
            onClick={() => handleModeChange("expenses")}
            className={`py-2.5 text-[15px] font-heading font-semibold transition-colors ${
              formData.mode === "expenses" ? "border border-accent text-accent-700 bg-accent/8 -m-px" : "text-text hover:bg-text/5"
            }`}
          >
            Expense
          </button>
          <button
            type="button"
            onClick={() => handleModeChange("others")}
            className={`py-2.5 text-[15px] font-heading font-semibold transition-colors ${
              formData.mode === "others" ? "border border-accent text-accent-700 bg-accent/8 -m-px" : "text-text hover:bg-text/5"
            }`}
          >
            Party / other
          </button>
        </div>

        <form onSubmit={handlePost} className="space-y-7">
          
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

          {/* Paid To */}
          <div className="space-y-1.5">
            <label className="text-[13px] font-body text-neutral-600 font-medium">Paid to</label>
            <MasterAutocomplete
              value={formData.debit_ledger}
              onChange={val => setFormData({ ...formData, debit_ledger: val })}
              placeholder={formData.mode === 'expenses' ? 'Search expenses' : 'Search party'}
              confirmed={formData.mode === 'expenses' ? meta.expense_paid_to.map(l => l.name) : meta.party_paid_to.map(l => l.name)}
              createLabel={formData.mode === 'expenses' ? 'expense account' : 'party'}
              icon={User}
              inputClassName="bg-transparent border-divider text-[15px]"
            />
          </div>

          {/* Create Account Section */}
          {formData.mode === "others" && !showCreateAccount && (
            <button
              type="button"
              onClick={() => setShowCreateAccount(true)}
              className="text-sm text-accent-700 font-semibold flex items-center gap-1 ml-1 -mt-3"
            >
              <span>+</span> Create new account
            </button>
          )}

          {formData.mode === "others" && showCreateAccount && (
            <div className="bg-accent/8 p-4 rounded border border-accent/30 space-y-4">
              <h4 className="text-sm font-bold text-accent-700">Create Account</h4>
              <Input
                label="Account Name"
                placeholder="e.g. Director Drawings"
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

          {/* Store */}
          {formData.mode === "expenses" && (
            <div className="space-y-1.5">
              <label className="text-[13px] font-body text-neutral-600 font-medium">Store</label>
              <MasterAutocomplete
                value={formData.cost_center}
                onChange={val => setFormData({ ...formData, cost_center: val })}
                placeholder="Select store"
                confirmed={meta.stores}
                createLabel="store"
                icon={Store}
                inputClassName="bg-transparent border-divider text-[15px]"
                disabled={!!lockedStore}
              />
            </div>
          )}

          {/* Paid From */}
          <div className="space-y-1.5">
            <label className="text-[13px] font-body text-neutral-600 font-medium">Paid from</label>
            <MasterAutocomplete
              value={formData.credit_ledger}
              onChange={val => setFormData({ ...formData, credit_ledger: val })}
              placeholder="Select bank or cash"
              confirmed={meta.paid_from.map(l => l.name)}
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
            <Button type="submit" disabled={loading}>
              {loading ? "Saving..." : "Save payment"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
