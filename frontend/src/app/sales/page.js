'use client';
import { useState, useEffect } from "react";
import { useRouter } from 'next/navigation';
import { ArrowLeft, User, Store as StoreIcon, CloudOff, Cloud, ChevronDown, Search, X } from 'lucide-react';
import { Select } from '@/components/ui/Select';
import { TextArea } from '@/components/ui/TextArea';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import { SALES_LEDGERS } from '@/utils/salesLedgers';
import { amountInWords } from '@/utils/numberToWords';

function todayLocal() {
  return new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0];
}
function yesterdayLocal() {
  const d = new Date(Date.now() - new Date().getTimezoneOffset() * 60000 - 86400000);
  return d.toISOString().split('T')[0];
}
function formatDateLong(yyyy_mm_dd) {
  if (!yyyy_mm_dd) return '';
  const [y, m, d] = yyyy_mm_dd.split('-').map(Number);
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(y, m - 1, d));
}
function formatDateShort(yyyy_mm_dd) {
  if (!yyyy_mm_dd) return '';
  const [y, m, d] = yyyy_mm_dd.split('-').map(Number);
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).format(new Date(y, m - 1, d));
}

function EntryHeader({ title, voucher, onBack, step }) {
  return (
    <div className="flex items-center gap-1.5 px-3 py-2.5 border-b border-divider">
      <button onClick={onBack} aria-label="Back" className="w-11 h-11 flex items-center justify-center rounded-full hover:bg-text/6 transition-colors shrink-0">
        <ArrowLeft className="w-5 h-5" />
      </button>
      <div className="flex-1 font-heading font-semibold text-xl truncate">{title}</div>
      <span className="text-[11px] px-2.5 py-1 border border-divider rounded text-neutral-700 shrink-0">
        {step || `${voucher} voucher`}
      </span>
    </div>
  );
}

function DateChips({ value, onChange }) {
  const today = todayLocal();
  const yesterday = yesterdayLocal();
  const isToday = value === today;
  const isYesterday = value === yesterday;
  const isCustom = !isToday && !isYesterday;
  const chipClass = (active) => `h-11 px-4 rounded-full border text-[13.5px] transition-colors ${active ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider hover:border-text/40'}`;

  return (
    <div>
      <label className="block text-xs text-text/70 mb-1.5">Date</label>
      <div className="flex gap-2 flex-wrap items-center">
        <button type="button" onClick={() => onChange(today)} className={chipClass(isToday)}>Today</button>
        <button type="button" onClick={() => onChange(yesterday)} className={chipClass(isYesterday)}>Yesterday</button>
        <label className={`flex items-center gap-2 h-11 px-3 rounded-full border cursor-pointer ${isCustom ? 'border-accent text-accent-700 bg-accent/8' : 'border-divider hover:border-text/40'}`}>
          <input type="date" value={value} max={today} onChange={e => onChange(e.target.value)} className="bg-transparent outline-none text-[13.5px] w-[120px]" />
        </label>
      </div>
      <div className="text-xs text-neutral-700 mt-1.5">{formatDateLong(value)}</div>
    </div>
  );
}

function AmountField({ label, value, onChange, amountRef }) {
  const num = parseFloat(value) || 0;
  return (
    <div>
      <label className="block text-xs text-text/70 mb-1">{label}</label>
      <div className="flex items-baseline gap-1.5 border-b-[1.5px] border-divider focus-within:border-accent pb-1 transition-colors">
        <span className="font-heading text-3xl text-neutral-600">₹</span>
        <input
          ref={amountRef}
          inputMode="decimal"
          value={value}
          onChange={e => {
            const digits = e.target.value.replace(/[^0-9.]/g, '');
            const firstDot = digits.indexOf('.');
            onChange(firstDot === -1 ? digits : digits.slice(0, firstDot + 1) + digits.slice(firstDot + 1).replace(/\./g, ''));
          }}
          placeholder="0"
          className="flex-1 min-w-0 border-0 bg-transparent outline-none font-heading text-5xl leading-tight [font-feature-settings:'tnum']"
        />
      </div>
      <div className="text-[12.5px] italic text-neutral-700 mt-1.5 min-h-[18px]">
        {num > 0 ? amountInWords(num) : ''}
      </div>
    </div>
  );
}

function OfflineNotice() {
  const { isOnline } = useSyncStatus();
  if (isOnline !== false) return null;
  return (
    <div className="flex gap-2 items-start text-[12.5px] text-neutral-700 leading-snug">
      <CloudOff className="w-3.5 h-3.5 text-accent-700 mt-0.5 shrink-0" />
      You&apos;re offline. This will be saved on this phone and sent to Tally when you reconnect.
    </div>
  );
}

// Full-screen party picker (Customer / Ledger). "Customer / Ledger" covers
// both a real customer (credit sale) and a cash/UPI settlement mode
// (walk-in sale) -- both lists are shown together under one group, matching
// what the backend itself treats as an equally valid party for a voucher.
function SearchPicker({ options, value, onSelect, onClose, title = "Customers", placeholder = "Search customers", typeLabel = "Customer" }) {
  const [query, setQuery] = useState('');
  const router = useRouter();
  const filtered = options.filter(o => o.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="fixed inset-0 z-50 bg-bg flex flex-col">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-divider">
        <div className="flex-1 flex items-center gap-2 h-[46px] px-3 border border-accent rounded-md">
          <Search className="w-4 h-4 text-neutral-600 shrink-0" />
          <input
            autoFocus
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={placeholder}
            className="flex-1 min-w-0 bg-transparent outline-none text-[15px]"
          />
        </div>
        <button onClick={onClose} className="h-11 px-2.5 text-accent-700">Cancel</button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-1">
        <div className="text-[10.5px] tracking-[0.12em] uppercase text-neutral-700 pt-4 pb-1">{title}</div>
        {filtered.length === 0 ? (
          <p className="text-sm text-neutral-600 text-center py-8">No matches.</p>
        ) : filtered.map(name => (
          <button
            key={name}
            onClick={() => onSelect(name)}
            className="w-full flex items-center justify-between gap-3 py-3.5 border-b border-divider text-left hover:bg-text/4 transition-colors"
          >
            <span className="min-w-0">
              <span className="block text-[15px] truncate">{name}</span>
              <span className="block text-sm text-neutral-700 mt-0.5">{name === value ? 'Selected' : typeLabel}</span>
            </span>
          </button>
        ))}
        <button
          onClick={() => router.push('/masters')}
          className="w-full text-center text-sm text-neutral-700 py-4 hover:text-accent-700 transition-colors"
        >
          Not listed? <span className="text-accent-700 underline underline-offset-2">Open masters</span>
        </button>
      </div>
    </div>
  );
}

function ReviewRow({ label, value }) {
  return (
    <div className="flex justify-between gap-4 py-3.5 border-b border-divider text-sm">
      <span className="text-neutral-700 shrink-0">{label}</span>
      <span className="text-right">{value || '—'}</span>
    </div>
  );
}

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
    date: todayLocal(),
  });

  const [step, setStep] = useState('form'); // 'form' | 'review' | 'saved'
  const [pickerOpen, setPickerOpen] = useState(false);
  const [storePickerOpen, setStorePickerOpen] = useState(false);
  const [posting, setPosting] = useState(false);
  const [saveResult, setSaveResult] = useState(null); // { status, queueCount }
  const [meta, setMeta] = useState({ customers: [], stores: ["Mahagun", "Vvip", "Gulshan"] });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormData(prev => ({ ...prev, store: lockedStore || prev.store }));

    fetch("/api/sales/metadata")
      .then(res => res.json())
      .then(data => setMeta(data))
      .catch(err => console.error("Failed to load metadata", err));
  }, [lockedStore]);

  const partyOptions = Array.from(new Set([...meta.customers, ...SALES_LEDGERS])).sort((a, b) => a.localeCompare(b));

  const resetForm = () => {
    setFormData({ ledger: "", amount: "", narration: "", store: lockedStore || "", date: todayLocal() });
  };

  const handleReview = (e) => {
    e.preventDefault();
    if (!formData.amount || parseFloat(formData.amount) <= 0) { showToast("Enter the amount", "error"); return; }
    if (!formData.ledger) { showToast("Choose a customer or ledger", "error"); return; }
    if (!formData.store) { showToast("Select a store", "error"); return; }
    setStep('review');
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
          ledger: formData.ledger,
          amount: parseFloat(formData.amount),
          tally_date: formData.date.replace(/-/g, ''),
          narration: finalNarration,
          store: formData.store,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Failed to post to Tally");

      let queueCount = null;
      if (data.status !== 'success') {
        try {
          const statsRes = await fetch('/api/dashboard/stats');
          if (statsRes.ok) queueCount = (await statsRes.json()).queue_count;
        } catch { }
      }
      setSaveResult({ status: data.status, queueCount });
      setStep('saved');
    } catch (error) {
      showToast(error.message || "Network error while posting", "error");
    }
    setPosting(false);
  };

  if (pickerOpen) {
    return (
      <SearchPicker
        options={partyOptions}
        value={formData.ledger}
        onSelect={name => { setFormData(f => ({ ...f, ledger: name })); setPickerOpen(false); }}
        onClose={() => setPickerOpen(false)}
        title="Customers"
        placeholder="Search customers"
        typeLabel="Customer"
      />
    );
  }

  if (storePickerOpen) {
    return (
      <SearchPicker
        options={meta.stores}
        value={formData.store}
        onSelect={name => { setFormData(f => ({ ...f, store: name })); setStorePickerOpen(false); }}
        onClose={() => setStorePickerOpen(false)}
        title="Stores"
        placeholder="Search stores"
        typeLabel="✓ In Tally"
      />
    );
  }

  if (step === 'saved' && saveResult) {
    const synced = saveResult.status === 'success';
    return (
      <div className="min-h-screen bg-bg flex flex-col">
        <div className="flex-1 overflow-y-auto px-6 pt-10 pb-6 flex flex-col items-center text-center">
          <span className="w-16 h-16 rounded-full border border-accent text-accent-700 flex items-center justify-center mb-3">
            {synced ? <Cloud className="w-7 h-7" /> : <CloudOff className="w-7 h-7" />}
          </span>
          <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">{synced ? 'Sent to Tally' : 'Saved offline'}</div>
          <h1 className="font-heading font-semibold text-3xl mt-1">{synced ? 'Sent to Tally' : 'Saved on this phone'}</h1>
          <p className="text-sm text-neutral-700 mt-2 max-w-xs">
            {synced
              ? 'This sale has been posted to Tally successfully.'
              : `It will go to Tally automatically when you're back online.${saveResult.queueCount != null ? ` ${saveResult.queueCount} entr${saveResult.queueCount === 1 ? 'y is' : 'ies are'} waiting.` : ''}`}
          </p>

          <div className="w-full mt-6 border-t border-divider text-left">
            <ReviewRow label="Amount" value={`₹${formData.amount}`} />
            <ReviewRow label="Customer / Ledger" value={formData.ledger} />
            <ReviewRow label="Date" value={formatDateShort(formData.date)} />
            <ReviewRow label="Store" value={formData.store} />
            {formData.narration && <ReviewRow label="Notes" value={formData.narration} />}
            <ReviewRow label="Voucher" value="Sales" />
          </div>
        </div>

        <div className="border-t border-divider px-5 py-4 flex flex-col gap-2.5">
          <button
            onClick={() => { resetForm(); setSaveResult(null); setStep('form'); }}
            className="h-[54px] border-[1.5px] border-accent rounded-md bg-accent/10 hover:bg-accent/16 active:bg-accent/24 text-accent-700 font-heading font-semibold text-lg transition-colors"
          >
            Record another sale
          </button>
          <button
            onClick={() => router.push('/dashboard')}
            className="h-12 border border-divider rounded-md font-heading font-semibold text-base hover:bg-text/6 transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    );
  }

  if (step === 'review') {
    return (
      <div className="min-h-screen bg-bg flex flex-col">
        <EntryHeader title="Review sale" onBack={() => setStep('form')} step="Step 2 of 2" />

        <div className="flex-1 overflow-y-auto px-5 pt-7 pb-6">
          <div className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">Check before saving</div>
          <div className="font-heading text-5xl leading-tight mt-1.5 [font-feature-settings:'tnum']">₹{formData.amount}</div>
          <div className="text-[12.5px] italic text-neutral-700 mt-1">{amountInWords(parseFloat(formData.amount) || 0)}</div>

          <div className="mt-6 border-t border-divider">
            <ReviewRow label="Date" value={formatDateLong(formData.date)} />
            <ReviewRow label="Customer / Ledger" value={formData.ledger} />
            <ReviewRow label="Store" value={formData.store} />
            <ReviewRow label="Notes" value={formData.narration} />
          </div>
        </div>

        <div className="border-t border-divider px-5 py-4 flex flex-col gap-2.5">
          <OfflineNotice />
          <button
            onClick={handlePost}
            disabled={posting}
            className="h-[54px] border-[1.5px] border-accent rounded-md bg-accent/10 hover:bg-accent/16 active:bg-accent/24 text-accent-700 font-heading font-semibold text-lg transition-colors disabled:opacity-50"
          >
            {posting ? "Sending..." : "Confirm & save sale"}
          </button>
          <button
            onClick={() => setStep('form')}
            disabled={posting}
            className="h-12 border border-divider rounded-md font-heading font-semibold text-base hover:bg-text/6 transition-colors"
          >
            Edit
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg flex flex-col">
      <EntryHeader title="Record a sale" voucher="Sales" onBack={() => router.back()} />

      <form onSubmit={handleReview} className="flex-1 overflow-y-auto px-5 pt-6 pb-6 flex flex-col gap-6">
        <DateChips value={formData.date} onChange={date => setFormData(f => ({ ...f, date }))} />

        <AmountField
          label="Total amount"
          value={formData.amount}
          onChange={amount => setFormData(f => ({ ...f, amount }))}
        />

        <div>
          <label className="block text-xs text-text/70 mb-1.5">Customer / Ledger</label>
          <button
            type="button"
            onClick={() => setPickerOpen(true)}
            className="w-full flex items-center gap-2.5 min-h-[56px] px-3 border border-divider rounded-md hover:border-text/45 transition-colors text-left"
          >
            <User className="w-4.5 h-4.5 text-neutral-600 shrink-0" />
            <span className={`flex-1 min-w-0 truncate text-[15px] ${formData.ledger ? '' : 'text-neutral-500'}`}>
              {formData.ledger || 'Search customers'}
            </span>
            <ChevronDown className="w-4.5 h-4.5 text-neutral-600 shrink-0" />
          </button>
        </div>

        <div>
          <label className="block text-xs text-text/70 mb-1.5">Store</label>
          <button
            type="button"
            onClick={() => !lockedStore && setStorePickerOpen(true)}
            className="w-full flex items-center gap-2.5 min-h-[56px] px-3 border border-divider rounded-md hover:border-text/45 transition-colors text-left"
            disabled={!!lockedStore}
          >
            <StoreIcon className="w-4.5 h-4.5 text-neutral-600 shrink-0" />
            <span className={`flex-1 min-w-0 truncate text-[15px] ${formData.store ? '' : 'text-neutral-500'}`}>
              {formData.store || 'Select store'}
            </span>
            <ChevronDown className="w-4.5 h-4.5 text-neutral-600 shrink-0" />
          </button>
        </div>

        <TextArea
          label="Notes (optional)"
          placeholder="Any extra details?"
          value={formData.narration}
          onChange={e => setFormData(f => ({ ...f, narration: e.target.value }))}
        />
      </form>

      <div className="border-t border-divider px-5 py-4 flex flex-col gap-2.5">
        <OfflineNotice />
        <button
          onClick={handleReview}
          className="h-[54px] border-[1.5px] border-accent rounded-md bg-accent/10 hover:bg-accent/16 active:bg-accent/24 text-accent-700 font-heading font-semibold text-lg transition-colors"
        >
          Review sale
        </button>
      </div>
    </div>
  );
}
