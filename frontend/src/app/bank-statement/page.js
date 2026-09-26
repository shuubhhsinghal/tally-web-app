'use client';
import { useState, useRef, useEffect, useMemo, Suspense } from "react";
import { useRouter, useSearchParams } from 'next/navigation';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { useUI } from '@/context/UIContext';
import { UploadCloud, CheckCircle2, AlertCircle, Trash2, ArrowDownLeft, ArrowUpRight, Landmark, ChevronLeft, ChevronDown, Clock } from "lucide-react";

function ReviewRow({ label, value }) {
  return (
    <div className="flex justify-between gap-4 py-3.5 border-b border-divider text-sm">
      <span className="text-neutral-700 shrink-0">{label}</span>
      <span className="text-right">{value ?? '—'}</span>
    </div>
  );
}

const TABS = [
  { key: 'transactions', label: 'Transactions' },
  { key: 'statement', label: 'Statement' },
  { key: 'loans', label: 'Loans' },
];

function fmtMoney(v) {
  const n = Math.abs(v || 0);
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}
function pad2(n) { return String(n).padStart(2, '0'); }
function fmtYYYYMMDD(d) { return `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}`; }
function dayLabel(yyyymmdd) {
  const d = new Date(+yyyymmdd.slice(0, 4), +yyyymmdd.slice(4, 6) - 1, +yyyymmdd.slice(6, 8));
  const today = new Date();
  const yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  if (fmtYYYYMMDD(d) === fmtYYYYMMDD(today)) return 'Today';
  if (fmtYYYYMMDD(d) === fmtYYYYMMDD(yesterday)) return 'Yesterday';
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).format(d).toUpperCase();
}

// --- Transactions tab -----------------------------------------------------

function TransactionsTab() {
  const [accounts, setAccounts] = useState(null);
  const [selected, setSelected] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [moneyFilter, setMoneyFilter] = useState('all');

  useEffect(() => {
    fetch('/api/bank-statement/accounts')
      .then(res => res.json())
      .then(json => {
        const list = json.accounts || [];
        setAccounts(list);
        if (list.length > 0) setSelected(list[0].name);
      })
      .catch(() => setAccounts([]));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    const end = new Date();
    const start = new Date(); start.setDate(end.getDate() - 90);
    fetch(`/api/bank-statement/transactions?ledger=${encodeURIComponent(selected)}&start_date=${fmtYYYYMMDD(start)}&end_date=${fmtYYYYMMDD(end)}`)
      .then(res => res.json())
      .then(json => setData(json))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [selected]);

  if (accounts === null) return <p className="text-sm text-neutral-600 text-center py-12">Loading&hellip;</p>;
  if (accounts.length === 0) return <p className="text-sm text-neutral-600 text-center py-12">No bank accounts found in Tally yet.</p>;

  const selectedAccount = accounts.find(a => a.name === selected);
  const movements = data?.movements || [];

  const sevenDaysAgo = fmtYYYYMMDD(new Date(Date.now() - 7 * 86400000));
  const last7 = movements.filter(m => m.date >= sevenDaysAgo);
  const in7 = last7.filter(m => m.amount > 0).reduce((a, m) => a + m.amount, 0);
  const out7 = last7.filter(m => m.amount < 0).reduce((a, m) => a + Math.abs(m.amount), 0);

  const filtered = movements.filter(m => moneyFilter === 'all' || (moneyFilter === 'in' ? m.amount > 0 : m.amount < 0));

  // Group by day, newest first, with running balance computed forward from
  // period_opening then reversed for display.
  let running = data?.period_opening ?? 0;
  const withBalance = movements.map(m => { running += m.amount; return { ...m, runningBalance: running }; });
  const byDate = {};
  filtered.forEach(m => {
    const withBal = withBalance.find(x => x === m || (x.date === m.date && x.voucher_id === m.voucher_id && x.amount === m.amount));
    const key = m.date;
    if (!byDate[key]) byDate[key] = [];
    byDate[key].push(withBal || m);
  });
  const dateKeys = Object.keys(byDate).sort((a, b) => b.localeCompare(a));

  return (
    <div>
      <div className="relative">
        <div className="flex gap-2.5 overflow-x-auto py-4 -mx-1 px-1" style={{ scrollbarWidth: 'none' }}>
          {accounts.map(a => (
            <button
              key={a.name}
              onClick={() => setSelected(a.name)}
              className={`shrink-0 min-w-[130px] text-left px-3.5 py-2.5 rounded-md border-[1.5px] transition-colors ${selected === a.name ? 'border-accent bg-accent/8' : 'border-neutral-400 hover:border-accent'}`}
            >
              <div className="text-sm whitespace-nowrap">{a.name}</div>
              <div className="font-heading font-semibold text-lg mt-0.5">{fmtMoney(a.balance)}</div>
            </button>
          ))}
        </div>
        <div className="pointer-events-none absolute right-0 top-0 bottom-0 w-10 bg-gradient-to-l from-bg to-transparent" />
      </div>

      {selectedAccount && (
        <>
          <div className="mt-2 mb-5">
            <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">{selectedAccount.name}</div>
            <div className="font-heading text-4xl leading-tight mt-1">{fmtMoney(selectedAccount.balance)}</div>
            <div className="text-sm text-neutral-700 mt-1.5">
              In {fmtMoney(in7)} &nbsp; Out {fmtMoney(out7)} &nbsp; last 7 days
            </div>
          </div>

          <div className="grid grid-cols-3 border border-divider rounded-md overflow-hidden mb-4">
            {[['all', 'All'], ['in', 'Money in'], ['out', 'Money out']].map(([k, label]) => (
              <button
                key={k}
                onClick={() => setMoneyFilter(k)}
                className={`h-10 text-sm transition-colors ${moneyFilter === k ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
              >
                {label}
              </button>
            ))}
          </div>

          {loading ? (
            <p className="text-sm text-neutral-600 text-center py-8">Loading&hellip;</p>
          ) : dateKeys.length === 0 ? (
            <p className="text-sm text-neutral-600 text-center py-8">No transactions in the last 90 days.</p>
          ) : (
            dateKeys.map(dateKey => {
              const rows = byDate[dateKey];
              const net = rows.reduce((a, m) => a + m.amount, 0);
              return (
                <div key={dateKey}>
                  <div className="flex justify-between items-baseline pt-4 pb-1 text-[11px] tracking-[0.08em] uppercase text-neutral-700">
                    <span>{dayLabel(dateKey)}</span>
                    <span>Net {net < 0 ? '− ' : ''}{fmtMoney(net)}</span>
                  </div>
                  {rows.map((m, i) => (
                    <div key={i} className="flex items-center gap-3 py-3.5 border-b border-divider">
                      <span className="w-10 h-10 rounded-full border border-divider flex items-center justify-center shrink-0 text-neutral-700">
                        {m.amount < 0 ? <ArrowUpRight className="w-5 h-5" /> : <ArrowDownLeft className="w-5 h-5" />}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="text-[15px] truncate">{m.reference || m.voucher_number || m.voucher_type || 'Entry'}</div>
                        <div className="text-sm text-neutral-700 truncate mt-0.5">
                          {m.is_pending ? <>Entered in app &middot; <span className="text-accent-700">Waiting for Tally</span></> : (m.voucher_number || m.voucher_type)}
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <div className={m.amount < 0 ? '' : 'text-accent-700'}>
                          {m.amount < 0 ? '− ' : '+ '}{fmtMoney(m.amount)}
                        </div>
                        <div className="text-sm text-neutral-700 mt-0.5">Bal {fmtMoney(m.runningBalance)}</div>
                      </div>
                    </div>
                  ))}
                </div>
              );
            })
          )}
        </>
      )}
    </div>
  );
}

// --- Loans tab (summary; full detail stays on the existing /loans page) ---

const EMPTY_LOAN_FORM = { lender_name: '', principal_amount: '', daily_amount: '', number_of_days: '', start_date: new Date().toISOString().slice(0, 10), received_into_ledger: '' };

function LoansTab({ onCount }) {
  const { showToast } = useUI();
  const [lenders, setLenders] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY_LOAN_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [cashBankLedgers, setCashBankLedgers] = useState([]);

  const fetchLenders = () => {
    fetch('/api/loans')
      .then(res => res.ok ? res.json() : { lenders: [] })
      .then(json => {
        setLenders(json.lenders || []);
        const count = (json.lenders || []).reduce((a, l) => a + (l.loans?.length || 0), 0);
        onCount(count);
      })
      .catch(() => setLenders([]));
  };

  useEffect(() => {
    fetchLenders();
    fetch('/api/bank-statement/ledgers')
      .then(res => res.ok ? res.json() : {})
      .then(data => {
        const names = Object.values(data)
          .filter(l => /bank|cash/i.test(l.parent || ''))
          .map(l => l.name)
          .sort((a, b) => a.localeCompare(b));
        setCashBankLedgers(names);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAddLoan = async () => {
    const principal = parseFloat(form.principal_amount);
    const daily = parseFloat(form.daily_amount);
    const days = parseInt(form.number_of_days, 10);
    if (!form.lender_name.trim()) { showToast('Enter the lender name', 'error'); return; }
    if (!principal || principal <= 0) { showToast('Enter the amount taken', 'error'); return; }
    if (!daily || daily <= 0) { showToast('Enter the daily installment', 'error'); return; }
    if (!days || days <= 0) { showToast('Enter the number of days', 'error'); return; }
    if (!form.received_into_ledger) { showToast('Select where the loan was received into', 'error'); return; }

    setSubmitting(true);
    try {
      const res = await fetch('/api/loans', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lender_name: form.lender_name.trim(),
          principal_amount: principal,
          daily_amount: daily,
          number_of_days: days,
          start_date: form.start_date,
          received_into_ledger: form.received_into_ledger,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to add loan');
      showToast('Loan added');
      setForm(EMPTY_LOAN_FORM);
      setAdding(false);
      fetchLenders();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  if (lenders === null) return <p className="text-sm text-neutral-600 text-center py-12">Loading&hellip;</p>;

  const totalOutstanding = lenders.reduce((a, l) => a + (l.total_outstanding || 0), 0);
  const totalDaily = lenders.reduce((a, l) => a + (l.loans || []).reduce((x, ln) => x + (ln.daily_amount || 0), 0), 0);

  return (
    <div className="pt-4 pb-8">
      <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">Loans outstanding</div>
      <div className="font-heading text-4xl leading-tight mt-1">{fmtMoney(totalOutstanding)}</div>
      <div className="text-sm text-neutral-700 mt-1.5">
        Across {lenders.length} lender{lenders.length === 1 ? '' : 's'} &middot; {fmtMoney(totalDaily)} a day in installments
      </div>

      <div className="border border-divider rounded-md mt-5">
        <div className="flex items-center gap-2.5 px-4 py-3.5 border-b border-divider">
          <Landmark className="w-4 h-4 text-accent-700" />
          <h3 className="font-heading font-semibold text-lg">Lenders</h3>
        </div>

        {lenders.length === 0 ? (
          <p className="text-sm text-neutral-600 text-center py-8">No loans recorded yet.</p>
        ) : lenders.map(l => {
          const taken = (l.loans || []).reduce((a, ln) => a + (ln.principal_amount || 0), 0);
          const isOpen = expanded === l.ledger_name;
          return (
            <div key={l.ledger_name} className="border-b border-divider last:border-b-0">
              <button
                onClick={() => setExpanded(isOpen ? null : l.ledger_name)}
                className="w-full flex items-center justify-between gap-3 px-4 py-3.5 text-left hover:bg-text/4 transition-colors"
              >
                <div className="min-w-0">
                  <div className="text-[15px] truncate">{l.lender_name}</div>
                  <div className="text-sm text-neutral-700 mt-0.5">
                    {(l.loans || []).length} loan{(l.loans || []).length === 1 ? '' : 's'} &middot; {fmtMoney(taken)} taken
                  </div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="font-heading font-semibold text-lg">{fmtMoney(l.total_outstanding)}</span>
                  <ChevronDown className={`w-4 h-4 text-neutral-500 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                </div>
              </button>
              {isOpen && (
                <div className="px-4 pb-4 flex flex-col gap-3">
                  {(l.loans || []).map((ln, i) => (
                    <div key={i} className="flex flex-col gap-1 border-t border-divider pt-3">
                      <div className="flex justify-between text-sm">
                        <div className="text-neutral-700">
                          {fmtMoney(ln.principal_amount)} taken {ln.start_date} &middot; {fmtMoney(ln.daily_amount)}/day &middot; {ln.number_of_days} days
                        </div>
                        <div className="shrink-0">{fmtMoney(ln.total_outstanding ?? ln.principal_amount)}</div>
                      </div>
                      <p className="text-[12.5px] text-neutral-600">
                        {fmtMoney(ln.total_interest)} interest over the loan &middot; received into {ln.received_into_ledger}
                      </p>
                      {ln.accrued_interest_to_date > 0 && (
                        <p className="text-[12.5px] text-neutral-600">
                          {fmtMoney(ln.accrued_interest_to_date)} interest posted to Tally so far
                        </p>
                      )}
                      {ln.this_month_interest_pending > 0 && (
                        <p className="text-[12.5px] text-accent-700">
                          {fmtMoney(ln.this_month_interest_pending)} interest posts to Tally on {ln.this_month_interest_posts_on}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {adding ? (
          <div className="p-5 border-t border-accent">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-heading font-semibold text-xl">Add a loan</h3>
              <button onClick={() => { setAdding(false); setForm(EMPTY_LOAN_FORM); }} className="text-sm text-neutral-700 hover:text-text">Cancel</button>
            </div>
            <div className="flex flex-col gap-3">
              <SearchableSelect
                label="Lender name"
                options={lenders.map(l => l.lender_name)}
                value={form.lender_name}
                onChange={val => setForm(f => ({ ...f, lender_name: val }))}
                onCreateNew={val => setForm(f => ({ ...f, lender_name: val }))}
                createLabel="lender"
                placeholder="Select or type a new lender"
              />
              <Input
                label="Amount taken (₹)"
                type="number"
                value={form.principal_amount}
                onChange={e => setForm(f => ({ ...f, principal_amount: e.target.value }))}
                placeholder="e.g. 1,00,000"
              />
              <div className="grid grid-cols-2 gap-3">
                <Input
                  label="Daily installment (₹)"
                  type="number"
                  value={form.daily_amount}
                  onChange={e => setForm(f => ({ ...f, daily_amount: e.target.value }))}
                  placeholder="e.g. 1,200"
                />
                <Input
                  label="Number of days"
                  type="number"
                  value={form.number_of_days}
                  onChange={e => setForm(f => ({ ...f, number_of_days: e.target.value }))}
                  placeholder="e.g. 100"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Input
                  label="Date"
                  type="date"
                  value={form.start_date}
                  onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))}
                />
                <Select
                  label="Received into"
                  value={form.received_into_ledger}
                  onChange={e => setForm(f => ({ ...f, received_into_ledger: e.target.value }))}
                >
                  <option value="" disabled>Select bank / cash</option>
                  {cashBankLedgers.map(name => <option key={name} value={name}>{name}</option>)}
                </Select>
              </div>
              <Button onClick={handleAddLoan} disabled={submitting} className="mt-1">
                {submitting ? 'Adding...' : 'Add loan'}
              </Button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setAdding(true)}
            className="w-full flex items-center justify-center gap-2 h-14 border-t border-dashed border-accent text-accent-700 hover:bg-accent/5 transition-colors"
          >
            + Add a loan
          </button>
        )}
      </div>
    </div>
  );
}

function BankStatementInteractive() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryDraftId = searchParams.get('draftId');
  const { showToast, showConfirmDialog } = useUI();

  const [tab, setTab] = useState('transactions');
  const [loanCount, setLoanCount] = useState(null);

  const [file, setFile] = useState(null);
  const [bankLedger, setBankLedger] = useState("");
  const [password, setPassword] = useState("");
  const [showPasswordPrompt, setShowPasswordPrompt] = useState(false);

  const [transactions, setTransactions] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isPosting, setIsPosting] = useState(false);
  const [filterMode, setFilterMode] = useState('all');

  // Set once a statement has been analyzed (draft created) or an existing
  // draft has been loaded via ?draftId= -- lets edits autosave back to it
  // instead of only living in this page's local state.
  const [draftId, setDraftId] = useState(null);
  const [draftLoading, setDraftLoading] = useState(!!queryDraftId);
  const [draftStatus, setDraftStatus] = useState(null); // 'PROCESSING' | 'READY' | 'FAILED'
  const [draftError, setDraftError] = useState(null);
  const [analyzedResult, setAnalyzedResult] = useState(null);
  const skipNextSaveRef = useRef(false);
  const saveTimerRef = useRef(null);
  const pollTimerRef = useRef(null);

  const [ledgerCache, setLedgerCache] = useState({});
  const [allRules, setAllRules] = useState({});
  const [showRules, setShowRules] = useState(false);
  const [editingRule, setEditingRule] = useState(null);
  const [quickRuleModal, setQuickRuleModal] = useState(null);
  const [isSavingRule, setIsSavingRule] = useState(false);
  const [deletingRuleId, setDeletingRuleId] = useState(null);
  const fileInputRef = useRef(null);

  const bankOptions = useMemo(() => {
    return Object.values(ledgerCache)
      .filter(l => l.parent === "Bank Accounts" || l.parent === "Bank OD A/c")
      .map(l => l.name)
      .sort((a, b) => a.localeCompare(b));
  }, [ledgerCache]);

  // ledgerCache is keyed by a lowercased normalized name (for case-insensitive
  // lookups elsewhere in this file) -- the properly-cased display name lives
  // in each entry's own .name field.
  const ledgerNameOptions = useMemo(() => {
    return Object.values(ledgerCache)
      .map(l => l.name)
      .sort((a, b) => a.localeCompare(b));
  }, [ledgerCache]);

  useEffect(() => {
    // Never touch bankLedger while viewing an existing draft -- its
    // bank_ledger_name (loaded below from the draft itself) is the
    // authoritative account this statement was uploaded and mapped against.
    // Without this guard, any mismatch against the current bankOptions list
    // (e.g. ledgerCache still loading, or the ledger renamed in Tally since
    // upload) would silently fall back to whichever bank account happens to
    // sort first, and every voucher on "Push to Tally" would then post
    // against the wrong bank ledger with no warning to the user.
    if (queryDraftId) return;
    if (bankOptions.length > 0 && (!bankLedger || !bankOptions.includes(bankLedger))) {
      setBankLedger(bankOptions[0]);
    }
  }, [bankOptions, bankLedger, queryDraftId]);

  const fetchAllRules = async () => {
    try {
      const res = await fetch("/api/bank-statement/mappings");
      if (res.ok) {
        const data = await res.json();
        const grouped = {};
        data.forEach(mapping => {
          const bankName = mapping.bank_account_name;
          if (!grouped[bankName]) grouped[bankName] = [];
          grouped[bankName].push(mapping);
        });
        setAllRules(grouped);
      }
    } catch (e) {
      console.error("Failed to load all rules", e);
    }
  };

  useEffect(() => {
    fetchAllRules();
    fetchLedgers();
  }, []);

  const fetchLedgers = async () => {
    try {
      const res = await fetch("/api/bank-statement/ledgers");
      if (res.ok) setLedgerCache(await res.json());
    } catch (e) {
      console.error("Failed to load ledgers", e);
    }
  };

  // Loads a statement previously saved to Review (via ?draftId=) so mapping
  // doesn't have to happen in the same sitting as the upload. While the
  // background extraction is still running, keeps polling every 5s until it
  // flips to READY (or FAILED) -- mirrors the Review inbox's own poll for a
  // PROCESSING purchase draft.
  useEffect(() => {
    if (!queryDraftId) return;
    let cancelled = false;

    const load = (silent) => {
      fetch(`/api/bank-statement/drafts/${queryDraftId}`)
        .then(res => { if (!res.ok) throw new Error('Failed to load this statement'); return res.json(); })
        .then(data => {
          if (cancelled) return;
          setDraftId(data.id);
          setDraftStatus(data.status);
          setDraftError(data.error_message || null);
          if (data.status === 'READY') {
            skipNextSaveRef.current = true;
            setBankLedger(data.bank_ledger_name);
            setTransactions(data.draft_data?.transactions || []);
          } else if (data.status === 'PROCESSING') {
            pollTimerRef.current = setTimeout(() => load(true), 5000);
          }
        })
        .catch(() => { if (!cancelled) showToast("Couldn't load this statement draft", 'error'); })
        .finally(() => { if (!cancelled && !silent) setDraftLoading(false); });
    };

    load(false);
    return () => { cancelled = true; if (pollTimerRef.current) clearTimeout(pollTimerRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryDraftId]);

  // Autosaves mapping progress back to the draft as the user works through
  // it, so navigating away (or losing connectivity mid-review) never loses
  // ledger picks already made.
  useEffect(() => {
    if (!draftId || draftStatus !== 'READY') return;
    if (skipNextSaveRef.current) { skipNextSaveRef.current = false; return; }
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      fetch(`/api/bank-statement/drafts/${draftId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transactions, bank_ledger_name: bankLedger })
      }).catch(() => {});
    }, 800);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transactions, draftId, bankLedger, draftStatus]);

  const handleFileChange = (e) => {
    const selectedFile = e.target.files[0];
    if (selectedFile) setFile(selectedFile);
  };

  const handleAnalyze = async () => {
    if (!file) {
      showToast("Please select a file first.", 'error');
      return;
    }

    setIsProcessing(true);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("bank_ledger_name", bankLedger);
    if (password) fd.append("password", password);

    try {
      const res = await fetch("/api/bank-statement-proxy", {
        method: "POST",
        body: fd
      });
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        if (res.status === 403) {
          setShowPasswordPrompt(true);
          if (password) showToast("Incorrect password. Please try again.", 'error');
          return;
        }
        throw new Error(errorData.detail || "Extraction failed");
      }
      // The backend saves this straight to Review and starts reading it in
      // the background (a PDF's extraction can take a few minutes) -- it
      // returns right away with just a PROCESSING draft id.
      const data = await res.json();
      setShowPasswordPrompt(false);
      setAnalyzedResult({ id: data.id });
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setIsProcessing(false);
    }
  };

  const updateTransaction = (index, field, value) => {
    const newTxns = [...transactions];
    const tx = newTxns[index];

    if (field === 'ledger') {
      tx.ledger = value;
      tx.unmapped = false; // User manually selected something

      const ledgerData = ledgerCache[value.toLowerCase()];
      const requiresCC = ledgerData && typeof ledgerData === 'object' ? ledgerData.cost_centre : !!ledgerData;
      tx.missing_cost_center = requiresCC && !tx.cost_center;

      setTransactions(newTxns);
    }
    else if (field === 'cost_center') {
      tx.cost_center = value;
      const ledgerData = ledgerCache[tx.ledger?.toLowerCase()];
      const requiresCC = ledgerData && typeof ledgerData === 'object' ? ledgerData.cost_centre : !!ledgerData;
      tx.missing_cost_center = requiresCC && !value;
      setTransactions(newTxns);
    }
  };

  const [lastRemoved, setLastRemoved] = useState(null); // { tx, index }
  const undoTimerRef = useRef(null);

  const removeTransaction = (index) => {
    const tx = transactions[index];
    setTransactions(transactions.filter((_, i) => i !== index));
    setLastRemoved({ tx, index });
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    undoTimerRef.current = setTimeout(() => setLastRemoved(null), 5000);
  };

  const undoRemoveTransaction = () => {
    if (!lastRemoved) return;
    const newTxns = [...transactions];
    newTxns.splice(lastRemoved.index, 0, lastRemoved.tx);
    setTransactions(newTxns);
    setLastRemoved(null);
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
  };

  const dismissTransferMatch = (index) => {
    const newTxns = [...transactions];
    newTxns[index] = { ...newTxns[index], transfer_match: null };
    setTransactions(newTxns);
  };

  const [mergingIndex, setMergingIndex] = useState(null);

  const mergeAsTransfer = async (index) => {
    const tx = transactions[index];
    const match = tx.transfer_match;
    if (!match || match.source !== 'pending') return;

    setMergingIndex(index);
    try {
      const res = await fetch('/api/bank-statement/merge-transfer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          queue_id: match.queue_id,
          bank_ledger_name: bankLedger,
          date: tx.date,
          withdraw: tx.withdraw,
          deposit: tx.deposit,
          raw_narration: tx.raw_narration,
        }),
      });
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || "Could not merge this transfer.");
      }
      setTransactions(transactions.filter((_, i) => i !== index));
      showToast("Merged into a single transfer entry between your accounts");
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setMergingIndex(null);
    }
  };

  const handleDeleteRule = async (mappingId, keyword) => {
    showConfirmDialog({
      title: "Delete this mapping rule?",
      message: `Are you sure you want to delete the mapping for "${keyword}"?`,
      confirmText: "Delete",
      cancelText: "Cancel",
      onConfirm: async () => {
        setDeletingRuleId(mappingId);
        try {
          const res = await fetch(`/api/bank-statement/mappings/${mappingId}`, {
            method: 'DELETE'
          });
          if (!res.ok) throw new Error("Delete failed");
          showToast(`Rule deleted`);
          fetchAllRules();
        } catch (e) {
          showToast("Failed to delete rule", 'error');
        } finally {
          setDeletingRuleId(null);
        }
      }
    });
  };

  const handleSaveRule = async () => {
    if (!editingRule.keyword || !editingRule.target_ledger) {
      showToast("Keyword and Target Ledger are required.", "error");
      return;
    }

    setIsSavingRule(true);
    const payload = {
      bank: bankLedger,
      keyword: editingRule.keyword.trim(),
      target_ledger: editingRule.target_ledger.trim(),
      cost_center: editingRule.cost_center ? editingRule.cost_center.trim() : null
    };

    try {
      const url = editingRule.id
        ? `/api/bank-statement/mappings/${editingRule.id}`
        : `/api/bank-statement/mappings`;

      const method = editingRule.id ? "PUT" : "POST";

      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!res.ok) throw new Error("Failed to save rule");

      await fetchAllRules();
      setEditingRule(null);
      showToast(editingRule.id ? "Rule updated" : "Rule created", "success");
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setIsSavingRule(false);
    }
  };

  const handlePostToTally = async () => {
    const hasMissingCC = transactions.some(tx => tx.missing_cost_center && !tx.cost_center);
    if (hasMissingCC) {
      showToast("Please resolve all missing cost centers before posting.", 'error');
      return;
    }

    setIsPosting(true);
    try {
      const payload = {
        transactions,
        bank_ledger_name: bankLedger,
        draft_id: draftId
      };

      const res = await fetch("/api/bank-statement/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Post failed");

      if (data.status === 'failed') {
        // Nothing was actually posted or queued (every transaction was
        // individually rejected by Tally, or a hard error) -- surface this
        // as an error and let the user reassess rather than navigating away
        // as if it went through.
        showToast(data.message, 'error');
        return;
      }

      // 'partial' (some transactions posted/queued, some rejected) and
      // 'success' both still navigate away: every transaction is durably
      // recorded either way (SYNCED, PENDING, or FAILED in the queue), so
      // any rejected ones remain visible and actionable from the dashboard's
      // Failed section rather than blocking on them here.
      showToast(data.message, data.status === 'partial' ? 'error' : 'success');
      router.push('/review');
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setIsPosting(false);
    }
  };

  const formatDate = (ds) => {
    if (!ds) return "";
    if (ds.length === 8) {
      return `${ds.substring(6, 8)}/${ds.substring(4, 6)}/${ds.substring(0, 4)}`;
    }
    return ds;
  };

  if (analyzedResult) {
    return (
      <div className="min-h-screen bg-bg flex flex-col">
        <div className="flex-1 overflow-y-auto px-6 pt-10 pb-6 flex flex-col items-center text-center">
          <span className="w-16 h-16 rounded-full border border-accent text-accent-700 flex items-center justify-center mb-3">
            <Clock className="w-7 h-7" />
          </span>
          <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">Saved for review</div>
          <h1 className="font-heading font-semibold text-3xl mt-1">Analyzing your statement</h1>
          <p className="text-sm text-neutral-700 mt-2 max-w-xs">
            This can take a few minutes for a long statement. Check the Review tab shortly — it&apos;ll be ready to map ledgers and push to Tally.
          </p>

          <div className="w-full mt-6 border-t border-divider text-left">
            <ReviewRow label="Bank account" value={bankLedger} />
          </div>
        </div>

        <div className="border-t border-divider px-5 py-4 flex flex-col gap-2.5">
          <button
            onClick={() => router.push('/review')}
            className="h-[54px] border-[1.5px] border-accent rounded-md bg-accent/10 hover:bg-accent/16 active:bg-accent/24 text-accent-700 font-heading font-semibold text-lg transition-colors"
          >
            Go to Review
          </button>
          <button
            onClick={() => {
              setAnalyzedResult(null);
              setFile(null);
              setPassword("");
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
            className="h-12 border border-divider rounded-md font-heading font-semibold text-base hover:bg-text/6 transition-colors"
          >
            Upload another statement
          </button>
        </div>
      </div>
    );
  }

  if (draftLoading) {
    return (
      <div className="min-h-screen bg-bg pb-24">
        <TopBar title="Review" showBack onBack={() => router.push('/review')} />
        <p className="text-sm text-neutral-600 text-center py-16">Loading&hellip;</p>
      </div>
    );
  }

  if (draftStatus === 'PROCESSING') {
    return (
      <div className="min-h-screen bg-bg pb-24">
        <TopBar title="Review" showBack onBack={() => router.push('/review')} />
        <div className="max-w-md mx-auto p-4 pt-16 flex flex-col items-center text-center gap-3">
          <span className="w-14 h-14 rounded-full border-2 border-accent/30 border-t-accent-700 animate-spin shrink-0" />
          <h2 className="font-heading font-semibold text-xl mt-2">Still analyzing&hellip;</h2>
          <p className="text-sm text-neutral-700 max-w-xs">
            This statement is still being read. This page will update automatically once it&apos;s ready to map.
          </p>
        </div>
      </div>
    );
  }

  if (draftStatus === 'FAILED') {
    return (
      <div className="min-h-screen bg-bg pb-24">
        <TopBar title="Review" showBack onBack={() => router.push('/review')} />
        <div className="max-w-md mx-auto p-4 pt-16 flex flex-col items-center text-center gap-3">
          <span className="w-14 h-14 rounded-full border border-accent text-accent-700 flex items-center justify-center shrink-0">
            <AlertCircle className="w-6 h-6" />
          </span>
          <h2 className="font-heading font-semibold text-xl mt-2">Couldn&apos;t read this statement</h2>
          <p className="text-[13px] text-accent-700 max-w-xs">{draftError || 'Something went wrong reading this statement.'}</p>
          <button
            onClick={() => {
              showConfirmDialog({
                title: 'Delete this statement?',
                message: 'This removes it from Review. You can upload it again from the Statement tab.',
                confirmText: 'Delete',
                cancelText: 'Cancel',
                confirmColor: 'bg-red-600 hover:bg-red-700',
                onConfirm: async () => {
                  try {
                    await fetch(`/api/bank-statement/drafts/${draftId}`, { method: 'DELETE' });
                  } finally {
                    router.push('/review');
                  }
                }
              });
            }}
            className="mt-3 h-11 px-5 border border-divider rounded-md text-[15px] hover:bg-text/5 transition-colors"
          >
            Delete and try again
          </button>
        </div>
      </div>
    );
  }

  if (transactions.length > 0) {
    const unmappedCount = transactions.filter(t => t.missing_cost_center && !t.cost_center).length;

    return (
      <div className="min-h-screen bg-bg pb-36">
        <TopBar title="Review" showBack onBack={() => router.push('/review')} />
        <div className="max-w-md mx-auto p-4 space-y-4 mt-4">

          <Card className="flex flex-col gap-2">
            <h3 className="text-xs font-bold text-neutral-600 uppercase tracking-wider">Summary</h3>
            <div className="flex justify-between items-center">
              <p className="text-sm font-medium text-text">{transactions.length} transactions</p>
              {unmappedCount > 0 ? (
                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 px-2 py-1 rounded">
                  {unmappedCount} action{unmappedCount !== 1 && 's'} needed
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-neutral-700 px-2 py-1 rounded">
                  <CheckCircle2 className="w-3 h-3" /> Ready to push
                </span>
              )}
            </div>
          </Card>

          <div className="grid grid-cols-3 border border-divider rounded-md overflow-hidden">
            <button
              onClick={() => setFilterMode('all')}
              className={`h-10 text-[13px] transition-colors ${filterMode === 'all' ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
            >
              All
            </button>
            <button
              onClick={() => setFilterMode('mapped')}
              className={`h-10 text-[13px] transition-colors ${filterMode === 'mapped' ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
            >
              Mapped
            </button>
            <button
              onClick={() => setFilterMode('unmapped')}
              className={`h-10 text-[13px] transition-colors ${filterMode === 'unmapped' ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
            >
              Unmapped
            </button>
          </div>

          <div className="space-y-3">
            {transactions.map((tx, idx) => {
              if (filterMode === 'mapped' && tx.unmapped) return null;
              if (filterMode === 'unmapped' && !tx.unmapped) return null;

              const needsAction = tx.missing_cost_center && !tx.cost_center;

              return (
                <Card key={idx} className={`flex flex-col gap-2 p-3 ${needsAction ? 'border-accent' : ''}`}>
                  <div className="flex justify-between items-start">
                    <p className="text-xs font-semibold text-neutral-600">{formatDate(tx.date)}</p>
                    <div className="flex items-center gap-2">
                      {tx.withdraw > 0 ? (
                        <p className="text-sm font-bold text-text">- ₹ {tx.withdraw.toFixed(2)}</p>
                      ) : (
                        <p className="text-sm font-bold text-accent-700">+ ₹ {tx.deposit.toFixed(2)}</p>
                      )}
                      <button
                        onClick={() => removeTransaction(idx)}
                        title="Remove this transaction"
                        className="text-neutral-400 hover:text-red-500 transition-colors shrink-0"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  <p className="text-sm font-medium text-text break-all leading-tight">{tx.raw_narration}</p>

                  {tx.transfer_match && (
                    <div className="flex flex-col gap-1.5 p-2 rounded-md bg-accent/8 border border-accent/30">
                      <p className="text-xs text-accent-700 leading-snug">
                        {tx.transfer_match.source === 'pending' ? (
                          <>🔁 Possibly the same transfer as a pending entry from <b>{tx.transfer_match.bank_ledger_name}</b> on {formatDate(tx.transfer_match.date)} for ₹{tx.transfer_match.amount.toFixed(2)}{tx.transfer_match.reference_match ? " — looks like a confirmed NEFT match" : ""}</>
                        ) : (
                          <>Already recorded from <b>{tx.transfer_match.bank_ledger_name}</b> on {formatDate(tx.transfer_match.date)}. Use the trash icon above if this is a duplicate.</>
                        )}
                      </p>
                      <div className="flex gap-2">
                        {tx.transfer_match.source === 'pending' && (
                          <button
                            onClick={() => mergeAsTransfer(idx)}
                            disabled={mergingIndex === idx}
                            className="px-2 py-1 text-[10px] font-bold text-accent-700 border border-accent hover:bg-accent/10 disabled:opacity-50 rounded-md shrink-0 transition-colors"
                          >
                            {mergingIndex === idx ? "Merging..." : "Merge as Transfer"}
                          </button>
                        )}
                        <button
                          onClick={() => dismissTransferMatch(idx)}
                          className="px-2 py-1 text-[10px] font-bold text-accent-700 hover:underline rounded-md shrink-0 transition-colors"
                        >
                          Not a match
                        </button>
                      </div>
                    </div>
                  )}

                  <div className="mt-1 pt-2 border-t border-divider">
                    <div className="flex items-center gap-2">
                      <SearchableSelect
                        className="flex-1"
                        options={ledgerNameOptions}
                        value={tx.ledger}
                        onChange={(val) => updateTransaction(idx, 'ledger', val)}
                        onCreateNew={(val) => updateTransaction(idx, 'ledger', val)}
                        createLabel="ledger"
                        placeholder="Map to ledger..."
                        error={tx.unmapped}
                      />
                      {tx.unmapped && <span className="text-[10px] font-bold text-accent-700 bg-accent/8 px-1.5 py-1 rounded shrink-0">DEFAULT</span>}
                      {tx.ledger && (
                        <button
                          onClick={() => setQuickRuleModal({ keyword: tx.raw_narration, target_ledger: tx.ledger, cost_center: tx.cost_center || '' })}
                          className="px-2 py-1.5 text-[10px] font-bold text-accent-700 bg-accent/8 hover:bg-accent/15 rounded-md shrink-0 transition-colors"
                        >
                          + Rule
                        </button>
                      )}
                    </div>

                    {tx.missing_cost_center && (
                      <div className="flex flex-col gap-1 mt-2">
                        <label className="text-[10px] font-bold text-accent-700 uppercase tracking-wider flex items-center gap-1">
                          <AlertCircle className="w-3 h-3" /> Cost Center Required
                        </label>
                        <select
                          value={tx.cost_center || ''}
                          onChange={(e) => updateTransaction(idx, 'cost_center', e.target.value)}
                          className="w-full text-sm p-1.5 border border-accent bg-accent/5 text-text rounded-md focus:border-accent focus:ring-2 focus:ring-accent/20 outline-none"
                        >
                          <option value="">Select Store...</option>
                          <option value="Mahagun">Mahagun</option>
                          <option value="Vvip">Vvip</option>
                          <option value="Gulshan">Gulshan</option>
                        </select>
                      </div>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>

          {lastRemoved && (
            <div className="fixed bottom-[150px] left-0 right-0 mx-auto max-w-md px-4 z-20 flex justify-center">
              <div className="flex items-center gap-3 bg-surface border border-divider text-text text-xs font-medium px-4 py-2.5 rounded-full shadow-lg">
                <span>Transaction removed</span>
                <button onClick={undoRemoveTransaction} className="font-bold text-accent-700 hover:text-accent-800">
                  Undo
                </button>
              </div>
            </div>
          )}

          <div className="fixed bottom-[80px] left-0 right-0 mx-auto max-w-md p-4 bg-surface border-t border-divider z-10 flex gap-3 shadow-[0_-10px_15px_-3px_rgba(0,0,0,0.1)]">
            <Button variant="secondary" onClick={() => router.push('/review')} disabled={isPosting} className="flex-1">
              Cancel
            </Button>
            <Button onClick={handlePostToTally} disabled={isPosting || unmappedCount > 0} className="flex-1">
              {isPosting ? "Sending..." : "Push to Tally"}
            </Button>
          </div>

          {quickRuleModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
              <Card className="w-full max-w-sm shadow-2xl p-4 space-y-4">
                <h3 className="text-sm font-bold text-text">Add Rule</h3>

                <Input
                  label="Narration Contains"
                  value={quickRuleModal.keyword}
                  onChange={e => setQuickRuleModal({ ...quickRuleModal, keyword: e.target.value })}
                  placeholder="e.g. AMAZON, SWIGGY"
                />

                <SearchableSelect
                  label="Maps to ledger"
                  options={ledgerNameOptions}
                  value={quickRuleModal.target_ledger}
                  onChange={(val) => setQuickRuleModal({ ...quickRuleModal, target_ledger: val })}
                  onCreateNew={(val) => setQuickRuleModal({ ...quickRuleModal, target_ledger: val })}
                  createLabel="ledger"
                  placeholder="Select or type ledger..."
                />

                <Input
                  label="Cost Center (Optional)"
                  value={quickRuleModal.cost_center || ''}
                  onChange={e => setQuickRuleModal({ ...quickRuleModal, cost_center: e.target.value })}
                  placeholder="e.g. Main Branch"
                />

                <div className="flex gap-3 pt-2">
                  <Button variant="secondary" onClick={() => setQuickRuleModal(null)} disabled={isSavingRule} className="flex-1">
                    Cancel
                  </Button>
                  <Button
                    onClick={async () => {
                      setIsSavingRule(true);
                      try {
                        const payload = {
                          bank: bankLedger,
                          keyword: quickRuleModal.keyword.trim(),
                          target_ledger: quickRuleModal.target_ledger.trim(),
                          cost_center: quickRuleModal.cost_center ? quickRuleModal.cost_center.trim() : null
                        };
                        const res = await fetch(`/api/bank-statement/mappings`, {
                          method: 'POST',
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify(payload)
                        });
                        if (!res.ok) throw new Error("Failed to save rule");
                        await fetchAllRules();

                        const keyword = quickRuleModal.keyword.trim().toLowerCase();
                        const targetLedger = quickRuleModal.target_ledger.trim();
                        const targetCC = quickRuleModal.cost_center ? quickRuleModal.cost_center.trim() : null;

                        const newTxns = [...transactions];
                        let updatedCount = 0;
                        newTxns.forEach(tx => {
                          if (tx.raw_narration.toLowerCase().includes(keyword)) {
                            tx.ledger = targetLedger;
                            tx.unmapped = false;
                            if (targetCC) tx.cost_center = targetCC;

                            const ledgerData = ledgerCache[targetLedger.toLowerCase()];
                            const requiresCC = ledgerData && typeof ledgerData === 'object' ? ledgerData.cost_centre : !!ledgerData;
                            tx.missing_cost_center = requiresCC && !tx.cost_center;
                            updatedCount++;
                          }
                        });
                        setTransactions(newTxns);

                        showToast(`Rule created and applied to ${updatedCount} transaction${updatedCount !== 1 ? 's' : ''}`, "success");
                        setQuickRuleModal(null);
                      } catch (e) {
                        showToast(e.message, "error");
                      } finally {
                        setIsSavingRule(false);
                      }
                    }}
                    disabled={!quickRuleModal.keyword || !quickRuleModal.target_ledger || isSavingRule}
                    className="flex-1"
                  >
                    {isSavingRule ? "Saving..." : "Save"}
                  </Button>
                </div>
              </Card>
            </div>
          )}
        </div>
      </div>
    );
  }

  const bankRules = allRules[bankLedger] || [];

  return (
    <div className="min-h-screen bg-bg pb-20">
      <TopBar title="Bank" kicker="Balances" />

      <div className="max-w-md mx-auto px-5">
        <div className="grid grid-cols-3 border border-divider rounded-md overflow-hidden my-4">
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`h-11 text-[13px] transition-colors ${tab === t.key ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
            >
              {t.label}{t.key === 'loans' && loanCount != null && loanCount > 0 ? ` · ${loanCount}` : ''}
            </button>
          ))}
        </div>

        {tab === 'transactions' && <TransactionsTab />}

        {tab === 'loans' && <LoansTab onCount={setLoanCount} />}

        {tab === 'statement' && (
          showRules ? (
            <div className="pb-8">
              <button onClick={() => setShowRules(false)} className="flex items-center gap-1 text-accent-700 text-sm py-4">
                <ChevronLeft className="w-4 h-4" /> Bank statement
              </button>

              <h2 className="font-heading font-semibold text-2xl">Mapping rules</h2>
              <p className="text-sm text-neutral-700 mt-1">When a statement line&apos;s narration contains a word, it&apos;s posted to that ledger automatically.</p>

              <div className="mt-5">
                <Select label="Bank account" value={bankLedger} onChange={e => setBankLedger(e.target.value)}>
                  {bankOptions.map(b => <option key={b} value={b}>{b}</option>)}
                </Select>
              </div>

              <div className="mt-2">
                {bankRules.map(rule => (
                  <div key={rule.id} className="flex items-center justify-between gap-3 py-3.5 border-b border-divider">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap text-[15px]">
                        <span className="text-neutral-700">contains</span>
                        <span className="px-2 py-0.5 border border-accent rounded text-accent-700 text-sm">{rule.keyword}</span>
                        <span className="text-neutral-500">&rsaquo;</span>
                        <span>{rule.target_ledger}</span>
                      </div>
                      {rule.cost_center && (
                        <div className="text-sm text-neutral-700 mt-1">Cost centre: {rule.cost_center}</div>
                      )}
                    </div>
                    <button
                      onClick={() => handleDeleteRule(rule.id, rule.keyword)}
                      disabled={deletingRuleId === rule.id}
                      className="p-2 text-neutral-600 hover:text-red-600 transition-colors shrink-0"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>

              {editingRule ? (
                <div className="border border-accent rounded-md p-5 mt-3">
                  <h3 className="font-heading font-semibold text-xl mb-4">Add rule</h3>
                  <div className="flex flex-col gap-3">
                    <Input
                      label="Narration contains"
                      value={editingRule.keyword}
                      onChange={e => setEditingRule({ ...editingRule, keyword: e.target.value })}
                      placeholder="E.G. AMAZON, SWIGGY"
                    />
                    <SearchableSelect
                      label="Maps to ledger"
                      options={ledgerNameOptions}
                      value={editingRule.target_ledger}
                      onChange={(val) => setEditingRule({ ...editingRule, target_ledger: val })}
                      onCreateNew={(val) => setEditingRule({ ...editingRule, target_ledger: val })}
                      createLabel="ledger"
                      placeholder="Select or type ledger"
                    />
                    <Select
                      label="Cost centre (optional)"
                      value={editingRule.cost_center || ''}
                      onChange={e => setEditingRule({ ...editingRule, cost_center: e.target.value })}
                    >
                      <option value="">None</option>
                      <option value="Mahagun">Mahagun</option>
                      <option value="Vvip">Vvip</option>
                      <option value="Gulshan">Gulshan</option>
                    </Select>
                    <div className="flex gap-3 mt-1">
                      <Button variant="secondary" onClick={() => setEditingRule(null)} disabled={isSavingRule} className="flex-1">Cancel</Button>
                      <Button onClick={handleSaveRule} disabled={!editingRule.keyword || !editingRule.target_ledger || isSavingRule} className="flex-1">
                        {isSavingRule ? "Saving..." : "Save rule"}
                      </Button>
                    </div>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setEditingRule({ id: null, keyword: '', target_ledger: '', cost_center: '' })}
                  className="w-full flex items-center justify-center gap-2 h-14 border border-dashed border-accent rounded-md text-accent-700 mt-3 hover:bg-accent/5 transition-colors"
                >
                  + Add rule
                </button>
              )}
            </div>
          ) : (
            <div className="pb-8">
              <div className="mt-4">
                <Select label="Bank account" value={bankLedger} onChange={e => setBankLedger(e.target.value)}>
                  {bankOptions.map(b => <option key={b} value={b}>{b}</option>)}
                </Select>
              </div>

              <div className="border-2 border-dashed border-accent rounded-md p-10 text-center mt-5">
                <input
                  type="file"
                  accept=".pdf,.xlsx,.xls"
                  className="hidden"
                  id="file-upload"
                  onChange={handleFileChange}
                  ref={fileInputRef}
                />
                <label htmlFor="file-upload" className="cursor-pointer flex flex-col items-center">
                  <UploadCloud className="h-8 w-8 text-accent-700 mb-3" />
                  <span className="font-heading font-semibold text-xl w-full break-all">
                    {isProcessing ? "Analyzing statement..." : (file ? file.name : "Choose statement file")}
                  </span>
                  {!file && <span className="text-sm text-neutral-700 mt-1">PDF or Excel, as downloaded from net banking</span>}
                </label>
              </div>

              <div className="flex gap-4 mt-4">
                <Button variant="secondary" onClick={() => setShowRules(true)} className="flex-1">
                  Manage rules{bankRules.length > 0 ? ` · ${bankRules.length}` : ''}
                </Button>
                <Button onClick={handleAnalyze} disabled={!file || isProcessing} className="flex-1">
                  {isProcessing ? "Analyzing..." : "Analyze"}
                </Button>
              </div>
            </div>
          )
        )}
      </div>

      {showPasswordPrompt && (
        <>
          <div
            className="fixed inset-0 bg-black/40 z-40 transition-opacity"
            onClick={() => setShowPasswordPrompt(false)}
          />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
            <div className="bg-surface border border-divider rounded-lg shadow-lg w-full max-w-sm p-6 pointer-events-auto animate-in zoom-in-95">
              <h3 className="font-heading font-semibold text-xl mb-2">Password required</h3>
              <p className="text-neutral-700 mb-4">This PDF is encrypted. Enter its password to continue.</p>
              <Input
                label="PDF password"
                type="password"
                autoFocus
                placeholder="Enter password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && password && !isProcessing) handleAnalyze(); }}
              />
              <div className="flex gap-3 mt-6">
                <Button variant="secondary" onClick={() => setShowPasswordPrompt(false)} className="flex-1">Cancel</Button>
                <Button onClick={handleAnalyze} disabled={!password || isProcessing} className="flex-1">
                  {isProcessing ? "Checking..." : "Unlock"}
                </Button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function BankStatementPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-bg pb-24">
        <p className="text-sm text-neutral-600 text-center py-16">Loading&hellip;</p>
      </div>
    }>
      <BankStatementInteractive />
    </Suspense>
  );
}
