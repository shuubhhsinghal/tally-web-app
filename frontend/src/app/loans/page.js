'use client';
import { useState, useEffect } from "react";
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { Landmark, Plus, ChevronDown, ChevronUp } from "lucide-react";

function formatRupees(amount) {
  return `₹${Math.round(amount || 0).toLocaleString('en-IN')}`;
}

function LoanDetailRow({ loan }) {
  return (
    <div className="px-4 py-3 flex flex-col gap-1">
      <div className="flex justify-between text-sm">
        <span className="font-bold text-gray-900 dark:text-white">{formatRupees(loan.principal_amount)}</span>
        <span className="text-gray-500">{loan.start_date}</span>
      </div>
      <p className="text-xs text-gray-500">
        {formatRupees(loan.daily_amount)}/day &middot; {loan.number_of_days} days &middot; {formatRupees(loan.total_interest)} interest &middot; received into {loan.received_into_ledger}
      </p>
      {loan.accrued_interest_to_date > 0 && (
        <p className="text-xs text-gray-500">
          {formatRupees(loan.accrued_interest_to_date)} interest posted to Tally so far
        </p>
      )}
      {loan.this_month_interest_pending > 0 && (
        <p className="text-xs text-teal-600 dark:text-teal-400">
          {formatRupees(loan.this_month_interest_pending)} interest posts to Tally on {loan.this_month_interest_posts_on}
        </p>
      )}
    </div>
  );
}

function LenderCard({ lender }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="px-4 py-3">
      <button onClick={() => setExpanded(e => !e)} className="flex items-center justify-between gap-3 w-full text-left">
        <div>
          <p className="font-bold text-gray-900 dark:text-white">{lender.lender_name}</p>
          <p className="text-xs text-gray-500">{lender.loans.length} loan{lender.loans.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-gray-900 dark:text-white">{formatRupees(lender.total_outstanding)}</span>
          {expanded ? <ChevronUp className="w-4 h-4 text-gray-400 flex-shrink-0" /> : <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />}
        </div>
      </button>
      {expanded && (
        <div className="flex flex-col divide-y divide-gray-100 dark:divide-gray-800 mt-3 -mx-4">
          {lender.loans.map(loan => <LoanDetailRow key={loan.id} loan={loan} />)}
        </div>
      )}
    </div>
  );
}

function LendersSection({ lenders }) {
  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
        <Landmark className="w-5 h-5 text-teal-600" /> Lenders
      </h2>
      <div className="divide-y divide-gray-100 dark:divide-gray-800 -mx-4">
        {lenders.map(lender => <LenderCard key={lender.lender_name} lender={lender} />)}
      </div>
    </Card>
  );
}

function AddLoanForm({ existingLenderNames, receivedIntoOptions, onCreated }) {
  const { showToast } = useUI();
  const [expanded, setExpanded] = useState(false);
  const [form, setForm] = useState({
    lender_name: "", principal_amount: "", daily_amount: "", number_of_days: "",
    start_date: new Date().toISOString().slice(0, 10), received_into_ledger: "",
  });
  const [creating, setCreating] = useState(false);

  const totalRepaymentAmount = (parseFloat(form.daily_amount) || 0) * (parseInt(form.number_of_days, 10) || 0);
  const totalInterest = totalRepaymentAmount - (parseFloat(form.principal_amount) || 0);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.lender_name.trim() || !form.principal_amount || !form.daily_amount || !form.number_of_days || !form.received_into_ledger) {
      showToast("Fill in every field", "error");
      return;
    }
    if (parseFloat(form.principal_amount) <= 0 || parseFloat(form.daily_amount) <= 0 || parseInt(form.number_of_days, 10) <= 0) {
      showToast("Amounts must be greater than zero", "error");
      return;
    }
    setCreating(true);
    try {
      const res = await fetch("/api/loans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lender_name: form.lender_name.trim(),
          principal_amount: parseFloat(form.principal_amount),
          daily_amount: parseFloat(form.daily_amount),
          number_of_days: parseInt(form.number_of_days, 10),
          start_date: form.start_date,
          received_into_ledger: form.received_into_ledger,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to add loan");
      showToast(data.message || "Loan added");
      setForm({ lender_name: "", principal_amount: "", daily_amount: "", number_of_days: "", start_date: new Date().toISOString().slice(0, 10), received_into_ledger: "" });
      setExpanded(false);
      onCreated();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setCreating(false);
    }
  };

  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="flex items-center gap-2 w-full py-3 px-4 rounded-xl border border-dashed border-gray-300 dark:border-gray-700 text-sm font-bold text-gray-500 dark:text-gray-400 hover:border-teal-400 hover:text-teal-600 dark:hover:text-teal-400 transition-colors"
      >
        <Plus className="w-4 h-4" /> Add a Loan
      </button>
    );
  }

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <Plus className="w-5 h-5 text-teal-600" /> Add a Loan
        </h2>
        <button onClick={() => setExpanded(false)} className="text-xs font-medium text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
          Cancel
        </button>
      </div>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <SearchableSelect
          label="Lender name"
          options={existingLenderNames}
          value={form.lender_name}
          onChange={name => setForm({ ...form, lender_name: name })}
          onCreateNew={name => setForm({ ...form, lender_name: name })}
          createLabel="a new lender"
          placeholder="Select or type a new lender..."
        />
        <Input label="Amount taken" type="number" step="0.01" min="0.01" value={form.principal_amount} onChange={e => setForm({ ...form, principal_amount: e.target.value })} placeholder="e.g. 100000" />
        <Input label="Daily installment" type="number" step="0.01" min="0.01" value={form.daily_amount} onChange={e => setForm({ ...form, daily_amount: e.target.value })} placeholder="e.g. 1200" />
        <Input label="Number of days" type="number" step="1" min="1" value={form.number_of_days} onChange={e => setForm({ ...form, number_of_days: e.target.value })} placeholder="e.g. 100" />
        {totalRepaymentAmount > 0 && (
          <p className="text-xs text-gray-500 -mt-1.5 ml-1">
            Total repayment: <span className="font-bold text-gray-700 dark:text-gray-300">{formatRupees(totalRepaymentAmount)}</span>
            {" "}&middot; Interest: <span className="font-bold text-gray-700 dark:text-gray-300">{formatRupees(totalInterest)}</span>
          </p>
        )}
        <Input label="Date" type="date" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })} />
        <Select label="Received into" value={form.received_into_ledger} onChange={e => setForm({ ...form, received_into_ledger: e.target.value })}>
          <option value="">Select bank/cash...</option>
          {receivedIntoOptions.map(b => <option key={b.name} value={b.name}>{b.name}</option>)}
        </Select>
        <Button type="submit" disabled={creating}>{creating ? "Adding..." : "Add Loan"}</Button>
      </form>
    </Card>
  );
}

export default function LoansPage() {
  const { user } = useAuth();
  const [lenders, setLenders] = useState([]);
  const [todayTotalDue, setTodayTotalDue] = useState(0);
  const [receivedIntoOptions, setReceivedIntoOptions] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    fetch("/api/loans")
      .then(res => res.json())
      .then(data => { setLenders(data.lenders || []); setTodayTotalDue(data.today_total_due || 0); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    if (!user?.is_owner) return;
    load();
    fetch("/api/payment/metadata")
      .then(res => res.json())
      .then(data => setReceivedIntoOptions(data.paid_from || []))
      .catch(() => {});
  }, [user]);

  if (!user?.is_owner) {
    return (
      <div className="max-w-2xl mx-auto space-y-6 p-4">
        <div className="bg-yellow-50 dark:bg-yellow-900/20 text-yellow-700 dark:text-yellow-400 p-4 rounded-xl text-sm border border-yellow-100 dark:border-yellow-900/30">
          Loans are only available to the owner account.
        </div>
      </div>
    );
  }

  const totalLoanCount = lenders.reduce((sum, l) => sum + l.loans.length, 0);
  const existingLenderNames = lenders.map(l => l.lender_name);

  return (
    <div className="max-w-2xl mx-auto space-y-6 p-4">
      <div className="flex items-center gap-3 border-b border-gray-100 dark:border-gray-800 pb-4">
        <div className="bg-teal-50 dark:bg-teal-900/30 p-2.5 rounded-xl">
          <Landmark className="w-6 h-6 text-teal-600" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Loans</h1>
          {totalLoanCount > 0 && (
            <p className="text-sm text-gray-500">
              {formatRupees(todayTotalDue)}/day across {totalLoanCount} loan{totalLoanCount !== 1 ? 's' : ''}
            </p>
          )}
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-gray-400">Loading...</p>
      ) : lenders.length === 0 ? (
        <p className="text-sm text-gray-400">No loans yet.</p>
      ) : (
        <LendersSection lenders={lenders} />
      )}

      <AddLoanForm existingLenderNames={existingLenderNames} receivedIntoOptions={receivedIntoOptions} onCreated={load} />
    </div>
  );
}
