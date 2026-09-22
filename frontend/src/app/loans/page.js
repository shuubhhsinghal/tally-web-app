'use client';
import { useState, useEffect } from "react";
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { Landmark, Plus, AlertTriangle, CheckCircle2, Wallet, ChevronDown, ChevronUp, Calculator } from "lucide-react";

function formatRupees(amount) {
  return `₹${Math.round(amount || 0).toLocaleString('en-IN')}`;
}

function RecordPaymentForm({ activeLoans, paidFromOptions, onDone }) {
  const { showToast } = useUI();
  const [lenderName, setLenderName] = useState("");
  const [amount, setAmount] = useState("");
  const [allocations, setAllocations] = useState({});
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [paidFrom, setPaidFrom] = useState("");
  const [saving, setSaving] = useState(false);

  if (activeLoans.length === 0) return null;

  // One dropdown entry per lender, not per loan -- a lender with more than
  // one active loan (a top-up, a new loan taken before an old one closed)
  // gets a single "split across N loans" step instead of you having to
  // record separate payments for each.
  const lenders = [];
  for (const loan of activeLoans) {
    let group = lenders.find(g => g.lenderName === loan.lender_name);
    if (!group) {
      group = { lenderName: loan.lender_name, loans: [] };
      lenders.push(group);
    }
    group.loans.push(loan);
  }

  const selectedGroup = lenders.find(g => g.lenderName === lenderName);
  const isMulti = !!selectedGroup && selectedGroup.loans.length > 1;
  const allocationTotal = Object.values(allocations).reduce((sum, v) => sum + (parseFloat(v) || 0), 0);

  const handleLenderChange = (name) => {
    setLenderName(name);
    const group = lenders.find(g => g.lenderName === name);
    if (!group) {
      setAmount("");
      setAllocations({});
    } else if (group.loans.length === 1) {
      setAmount(String(group.loans[0].daily_amount));
      setAllocations({});
    } else {
      const defaults = {};
      group.loans.forEach(l => { defaults[l.id] = String(l.daily_amount); });
      setAllocations(defaults);
      setAmount("");
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!lenderName) {
      showToast("Select which lender this payment is for", "error");
      return;
    }
    if (!paidFrom) {
      showToast("Select where this was paid from", "error");
      return;
    }

    setSaving(true);
    try {
      let res;
      if (isMulti) {
        const allocationList = selectedGroup.loans
          .map(l => ({ loan_id: l.id, amount: parseFloat(allocations[l.id]) || 0 }))
          .filter(a => a.amount > 0);
        if (allocationList.length === 0) {
          showToast("Enter at least one loan's amount", "error");
          setSaving(false);
          return;
        }
        res = await fetch("/api/loans/repay-combined", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lender_name: lenderName, date, paid_from: paidFrom, allocations: allocationList }),
        });
      } else {
        if (!amount || parseFloat(amount) <= 0) {
          showToast("Enter a valid amount", "error");
          setSaving(false);
          return;
        }
        res = await fetch(`/api/loans/${selectedGroup.loans[0].id}/repay`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ amount: parseFloat(amount), date, paid_from: paidFrom }),
        });
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to post payment");
      showToast(data.message || "Payment posted to Tally");
      setLenderName("");
      setAmount("");
      setAllocations({});
      setPaidFrom("");
      onDone();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="flex flex-col gap-3 border-teal-200 dark:border-teal-800">
      <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
        <Wallet className="w-5 h-5 text-teal-600" /> Record a Payment
      </h2>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <Select label="Lender" value={lenderName} onChange={e => handleLenderChange(e.target.value)}>
          <option value="">Select lender...</option>
          {lenders.map(g => (
            <option key={g.lenderName} value={g.lenderName}>
              {g.lenderName}{g.loans.length > 1 ? ` (${g.loans.length} loans)` : ''}
            </option>
          ))}
        </Select>

        {isMulti ? (
          <div className="flex flex-col gap-2 rounded-xl border border-gray-200 dark:border-gray-700 p-3">
            <p className="text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
              Split across {selectedGroup.loans.length} loans
            </p>
            {selectedGroup.loans.map(l => (
              <div key={l.id} className="flex items-center gap-3">
                <span className="flex-1 text-sm text-gray-600 dark:text-gray-300 truncate">
                  Started {l.start_date} &middot; usually {formatRupees(l.daily_amount)}/day
                </span>
                <div className="w-28">
                  <Input
                    type="number" step="0.01" min="0"
                    value={allocations[l.id] ?? ""}
                    onChange={e => setAllocations({ ...allocations, [l.id]: e.target.value })}
                  />
                </div>
              </div>
            ))}
            <div className="flex justify-between text-sm font-bold pt-2 border-t border-gray-100 dark:border-gray-800">
              <span className="text-gray-500 dark:text-gray-400">Total</span>
              <span className="text-gray-900 dark:text-white">{formatRupees(allocationTotal)}</span>
            </div>
          </div>
        ) : (
          <Input label="Amount" type="number" step="0.01" min="0.01" value={amount} onChange={e => setAmount(e.target.value)} />
        )}

        <div className="flex gap-3">
          <Input label="Date" type="date" value={date} onChange={e => setDate(e.target.value)} className="flex-1" />
          <div className="flex-1">
            <Select label="Paid from" value={paidFrom} onChange={e => setPaidFrom(e.target.value)}>
              <option value="">Select bank/cash...</option>
              {paidFromOptions.map(b => <option key={b.name} value={b.name}>{b.name}</option>)}
            </Select>
          </div>
        </div>
        <Button type="submit" disabled={saving}>{saving ? "Posting..." : "Push to Tally"}</Button>
      </form>
    </Card>
  );
}

function LoanCard({ loan }) {
  const behind = loan.behind_by;

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-bold text-gray-900 dark:text-white">{loan.lender_name}</p>
          <p className="text-xs text-gray-500">{formatRupees(loan.daily_amount)}/day &middot; started {loan.start_date}</p>
        </div>
        <span className={`text-xs font-bold px-2 py-1 rounded-full whitespace-nowrap ${loan.status === 'closed' ? 'bg-gray-100 text-gray-500 dark:bg-gray-800' : 'bg-teal-50 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400'}`}>
          {loan.status === 'closed' ? 'Closed' : 'Active'}
        </span>
      </div>

      <div className="flex justify-between text-sm">
        <span className="text-gray-500">Balance</span>
        <span className="font-bold text-gray-900 dark:text-white">{formatRupees(loan.balance)}</span>
      </div>
      <div className="flex justify-between text-sm">
        <span className="text-gray-500">Paid so far</span>
        <span className="text-gray-700 dark:text-gray-300">{formatRupees(loan.paid_to_date)}</span>
      </div>
      {loan.status === 'active' && (
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">Days left</span>
          <span className="text-gray-700 dark:text-gray-300">{loan.days_left}</span>
        </div>
      )}
      <div className="flex justify-between text-sm">
        <span className="text-gray-500">This month</span>
        <span className="text-gray-700 dark:text-gray-300">
          {formatRupees(loan.paid_this_month)} paid &middot; {formatRupees(loan.interest_accrued_this_month)} interest accrued
        </span>
      </div>

      {loan.status === 'active' && (
        <div className={`flex items-center gap-2 text-xs font-medium px-3 py-2 rounded-xl ${behind > 0 ? 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400' : 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'}`}>
          {behind > 0 ? <AlertTriangle className="w-4 h-4 flex-shrink-0" /> : <CheckCircle2 className="w-4 h-4 flex-shrink-0" />}
          {behind > 0
            ? `${formatRupees(behind)} behind schedule`
            : behind < 0 ? `${formatRupees(-behind)} ahead of schedule` : 'On schedule'}
        </div>
      )}
    </Card>
  );
}

function LenderRow({ lenderName, loans }) {
  const [expanded, setExpanded] = useState(false);
  const activeCount = loans.filter(l => l.status === 'active').length;
  const totalBalance = loans.reduce((sum, l) => sum + (l.balance || 0), 0);

  return (
    <div className="px-4 py-3">
      <button onClick={() => setExpanded(e => !e)} className="flex items-center justify-between gap-3 w-full text-left">
        <div>
          <p className="font-bold text-gray-900 dark:text-white">{lenderName}</p>
          <p className="text-xs text-gray-500">
            {loans.length} loan{loans.length !== 1 ? 's' : ''}{activeCount > 0 ? ` · ${activeCount} active` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-gray-900 dark:text-white">{formatRupees(totalBalance)}</span>
          {expanded ? <ChevronUp className="w-4 h-4 text-gray-400 flex-shrink-0" /> : <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />}
        </div>
      </button>
      {expanded && (
        <div className="flex flex-col gap-3 mt-3">
          {loans.map(loan => <LoanCard key={loan.id} loan={loan} />)}
        </div>
      )}
    </div>
  );
}

function LendersSection({ loansByLender }) {
  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
        <Landmark className="w-5 h-5 text-teal-600" /> Lenders
      </h2>
      <div className="divide-y divide-gray-100 dark:divide-gray-800 -mx-4">
        {loansByLender.map(group => (
          <LenderRow key={group.lenderName} lenderName={group.lenderName} loans={group.loans} />
        ))}
      </div>
    </Card>
  );
}

function OngoingLoanCalculator({ onApply }) {
  const [open, setOpen] = useState(false);
  const [takenOn, setTakenOn] = useState("");
  const [origPrincipal, setOrigPrincipal] = useState("");
  const [dailyAmount, setDailyAmount] = useState("");
  const [tenureDays, setTenureDays] = useState("");

  const result = (() => {
    const P = parseFloat(origPrincipal);
    const D = parseFloat(dailyAmount);
    const N = parseInt(tenureDays, 10);
    if (!takenOn || !(P > 0) || !(D > 0) || !(N > 0)) return null;
    const total = D * N;
    const start = new Date(takenOn + "T00:00:00");
    const today = new Date(new Date().toDateString());
    const daysElapsed = Math.min(N, Math.max(0, Math.round((today - start) / 86400000)));
    const expectedPaid = Math.min(daysElapsed * D, total);
    const principalPaid = expectedPaid * (P / total);
    const outstandingPrincipal = Math.round(Math.max(0, P - principalPaid) * 100) / 100;
    const remainingDays = N - daysElapsed;
    return { outstandingPrincipal, remainingDays, daysElapsed };
  })();

  const handleApply = () => {
    onApply({
      principal_amount: String(result.outstandingPrincipal),
      daily_amount: dailyAmount,
      number_of_days: String(Math.max(result.remainingDays, 1)),
    });
    setOpen(false);
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 text-xs font-bold text-teal-600 dark:text-teal-400 hover:underline w-fit"
      >
        <Calculator className="w-3.5 h-3.5" /> Already have an ongoing loan? Work out the outstanding balance
      </button>
    );
  }

  return (
    <div className="rounded-xl border border-dashed border-teal-300 dark:border-teal-800 bg-teal-50/50 dark:bg-teal-900/10 p-3 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-bold text-teal-700 dark:text-teal-400 flex items-center gap-1.5">
          <Calculator className="w-3.5 h-3.5" /> Outstanding balance calculator
        </p>
        <button type="button" onClick={() => setOpen(false)} className="text-xs font-medium text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
          Close
        </button>
      </div>
      <Input label="Loan taken on" type="date" value={takenOn} onChange={e => setTakenOn(e.target.value)} />
      <Input label="Original principal" type="number" step="0.01" min="0.01" value={origPrincipal} onChange={e => setOrigPrincipal(e.target.value)} placeholder="e.g. 100000" />
      <Input label="Daily amount" type="number" step="0.01" min="0.01" value={dailyAmount} onChange={e => setDailyAmount(e.target.value)} placeholder="e.g. 1200" />
      <Input label="Original tenure (days)" type="number" step="1" min="1" value={tenureDays} onChange={e => setTenureDays(e.target.value)} placeholder="e.g. 100" />
      {result && (
        <div className="text-xs text-gray-600 dark:text-gray-300 bg-white dark:bg-gray-800 rounded-lg p-3 space-y-1">
          <p>{result.daysElapsed} day{result.daysElapsed !== 1 ? 's' : ''} elapsed, assuming on-schedule payments.</p>
          <p>Outstanding principal today: <span className="font-bold text-gray-900 dark:text-white">{formatRupees(result.outstandingPrincipal)}</span></p>
          <p>Days remaining: <span className="font-bold text-gray-900 dark:text-white">{Math.max(result.remainingDays, 0)}</span></p>
          {result.remainingDays <= 0 && (
            <p className="text-amber-600 dark:text-amber-400 font-medium">This loan should already be fully repaid on schedule — double check before adding it.</p>
          )}
        </div>
      )}
      <Button type="button" variant="secondary" disabled={!result} onClick={handleApply}>
        Use these numbers
      </Button>
    </div>
  );
}

function AddLoanForm({ existingLenderNames, onCreated }) {
  const { showToast } = useUI();
  const [expanded, setExpanded] = useState(false);
  const [form, setForm] = useState({
    lender_name: "", principal_amount: "", daily_amount: "", number_of_days: "",
    start_date: new Date().toISOString().slice(0, 10),
  });
  const [creating, setCreating] = useState(false);

  const totalRepaymentAmount = (parseFloat(form.daily_amount) || 0) * (parseInt(form.number_of_days, 10) || 0);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.lender_name.trim() || !form.principal_amount || !form.daily_amount || !form.number_of_days) {
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
          total_repayment_amount: totalRepaymentAmount,
          daily_amount: parseFloat(form.daily_amount),
          start_date: form.start_date,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to create loan");
      showToast(`Loan from ${data.lender_name} added`);
      setForm({ lender_name: "", principal_amount: "", daily_amount: "", number_of_days: "", start_date: new Date().toISOString().slice(0, 10) });
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
        <OngoingLoanCalculator onApply={values => setForm({ ...form, ...values })} />
        <Input label="Principal amount" type="number" step="0.01" min="0.01" value={form.principal_amount} onChange={e => setForm({ ...form, principal_amount: e.target.value })} placeholder="e.g. 100000" />
        <Input label="Daily amount" type="number" step="0.01" min="0.01" value={form.daily_amount} onChange={e => setForm({ ...form, daily_amount: e.target.value })} placeholder="e.g. 1200" />
        <Input label="Number of days" type="number" step="1" min="1" value={form.number_of_days} onChange={e => setForm({ ...form, number_of_days: e.target.value })} placeholder="e.g. 100" />
        {totalRepaymentAmount > 0 && (
          <p className="text-xs text-gray-500 -mt-1.5 ml-1">
            Total repayment: <span className="font-bold text-gray-700 dark:text-gray-300">{formatRupees(totalRepaymentAmount)}</span>
          </p>
        )}
        <Input label="Start date" type="date" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })} />
        <Button type="submit" disabled={creating}>{creating ? "Adding..." : "Add Loan"}</Button>
      </form>
    </Card>
  );
}

export default function LoansPage() {
  const { user } = useAuth();
  const [loans, setLoans] = useState([]);
  const [todayTotalDue, setTodayTotalDue] = useState(0);
  const [paidFromOptions, setPaidFromOptions] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    fetch("/api/loans")
      .then(res => res.json())
      .then(data => { setLoans(data.loans || []); setTodayTotalDue(data.today_total_due || 0); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    if (!user?.is_owner) return;
    load();
    fetch("/api/payment/metadata")
      .then(res => res.json())
      .then(data => setPaidFromOptions(data.paid_from || []))
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

  const activeLoans = loans.filter(l => l.status === 'active');
  const loansByLender = [];
  for (const loan of loans) {
    let group = loansByLender.find(g => g.lenderName === loan.lender_name);
    if (!group) {
      group = { lenderName: loan.lender_name, loans: [] };
      loansByLender.push(group);
    }
    group.loans.push(loan);
  }
  const existingLenderNames = [...new Set(loans.map(l => l.lender_name))];

  return (
    <div className="max-w-2xl mx-auto space-y-6 p-4">
      <div className="flex items-center gap-3 border-b border-gray-100 dark:border-gray-800 pb-4">
        <div className="bg-teal-50 dark:bg-teal-900/30 p-2.5 rounded-xl">
          <Landmark className="w-6 h-6 text-teal-600" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Loans</h1>
          {activeLoans.length > 0 && (
            <p className="text-sm text-gray-500">
              {formatRupees(todayTotalDue)}/day across {activeLoans.length} active loan{activeLoans.length !== 1 ? 's' : ''}
            </p>
          )}
        </div>
      </div>

      {!loading && (
        <RecordPaymentForm activeLoans={activeLoans} paidFromOptions={paidFromOptions} onDone={load} />
      )}

      {loading ? (
        <p className="text-sm text-gray-400">Loading...</p>
      ) : loans.length === 0 ? (
        <p className="text-sm text-gray-400">No loans yet.</p>
      ) : (
        <LendersSection loansByLender={loansByLender} />
      )}

      <AddLoanForm existingLenderNames={existingLenderNames} onCreated={load} />
    </div>
  );
}
