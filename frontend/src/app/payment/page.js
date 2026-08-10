'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { TextArea } from '@/components/ui/TextArea';
import { useUI } from '@/context/UIContext';
import { useRouter } from 'next/navigation';

export default function PaymentVoucher() {
  const router = useRouter();
  const { showToast } = useUI();

  const [formData, setFormData] = useState({
    mode: "expenses", // 'expenses' or 'others'
    debit_ledger: "",
    credit_ledger: "",
    amount: "",
    date: new Date().toISOString().split('T')[0],
    cost_center: "",
    narration: ""
  });
  
  const [loading, setLoading] = useState(false);
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
        groups: data.groups || []
      })))
      .catch(err => console.error("Failed to load metadata", err));
  };

  useEffect(() => {
    fetchMetadata();
  }, []);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleModeChange = (mode) => {
    setFormData({ ...formData, mode, cost_center: mode === "others" ? "" : formData.cost_center, debit_ledger: "" });
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
      
      if (response.ok) {
        showToast("Payment saved");
        router.push('/dashboard');
      } else {
        showToast("Failed to post to Tally", "error");
      }
    } catch (error) {
      showToast("Network error while posting", "error");
    }
    setLoading(false);
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar title="Record a payment" showBack />
      
      <div className="max-w-md mx-auto p-4 mt-4 space-y-6">
        
        {/* Segmented Control */}
        <div className="flex bg-gray-200 dark:bg-gray-800 p-1 rounded-xl w-full">
          <button
            type="button"
            onClick={() => handleModeChange("expenses")}
            className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-colors ${
              formData.mode === "expenses" ? "bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white" : "text-gray-500 dark:text-gray-400"
            }`}
          >
            Expense
          </button>
          <button
            type="button"
            onClick={() => handleModeChange("others")}
            className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-colors ${
              formData.mode === "others" ? "bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white" : "text-gray-500 dark:text-gray-400"
            }`}
          >
            Party / Other
          </button>
        </div>

        <form onSubmit={handlePost} className="space-y-6">
          <Input 
            label="Date"
            type="date"
            name="date"
            value={formData.date}
            onChange={handleChange}
            required
          />

          <Input 
            label="Amount"
            type="number"
            step="0.01"
            name="amount"
            placeholder="e.g. 1500"
            value={formData.amount}
            onChange={handleChange}
            required
          />

          <div className="space-y-2">
            <Select 
              label="Paid to"
              name="debit_ledger"
              value={formData.debit_ledger}
              onChange={handleChange}
              required
            >
              <option value="">
                {formData.mode === 'expenses' 
                  ? (meta.expense_paid_to.length === 0 ? 'No eligible cost-centre ledgers found' : 'Select expense...')
                  : (meta.party_paid_to.length === 0 ? 'No accounts found' : 'Select party/other...')}
              </option>
              {(formData.mode === 'expenses' ? meta.expense_paid_to : meta.party_paid_to).map(l => (
                <option key={l.name} value={l.name}>{l.name} {l.is_pending ? '⏳ In Queue' : ''}</option>
              ))}
            </Select>
            
            {formData.mode === "others" && !showCreateAccount && (
              <button 
                type="button" 
                onClick={() => setShowCreateAccount(true)}
                className="text-sm text-teal-600 dark:text-teal-400 font-medium hover:underline flex items-center gap-1"
              >
                <span>+</span> Create new account
              </button>
            )}
            
            {formData.mode === "others" && showCreateAccount && (
              <div className="bg-gray-100 dark:bg-gray-800 p-4 rounded-xl border border-gray-200 dark:border-gray-700 space-y-4">
                <h4 className="text-sm font-bold text-gray-900 dark:text-white">Create Account</h4>
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
                  <Button type="button" variant="outline" onClick={() => setShowCreateAccount(false)} className="flex-1">
                    Cancel
                  </Button>
                  <Button type="button" onClick={handleCreateAccount} disabled={creatingAccount} className="flex-1">
                    {creatingAccount ? '...' : 'Create Account'}
                  </Button>
                </div>
              </div>
            )}
          </div>

          {formData.mode === "expenses" && (
            <Select 
              label="Store"
              name="cost_center"
              value={formData.cost_center}
              onChange={handleChange}
              required
            >
              <option value="">Select store...</option>
              {meta.stores.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </Select>
          )}

          <Select 
            label="Paid from"
            name="credit_ledger"
            value={formData.credit_ledger}
            onChange={handleChange}
            required
          >
            <option value="">
              {meta.paid_from.length === 0 ? 'No bank/cash accounts found' : 'Select bank/cash...'}
            </option>
            {meta.paid_from.map(b => (
              <option key={b.name} value={b.name}>{b.name} {b.is_pending ? '⏳ In Queue' : ''}</option>
            ))}
          </Select>

          <TextArea 
            label="Notes (Optional)"
            name="narration"
            placeholder="What was this payment for?"
            value={formData.narration}
            onChange={handleChange}
          />

          <Button type="submit" disabled={loading} className="mt-8">
            {loading ? "Saving..." : "Save payment"}
          </Button>
        </form>
      </div>
    </div>
  );
}
