'use client';

import { EmptyState } from '@/components/ui/EmptyState';
import { MoveRight } from 'lucide-react';

function fmtMoney2(v) {
  const n = Number(v || 0);
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function Row({ label, value }) {
  if (value === undefined || value === null || value === '') return null;
  return (
    <div className="flex justify-between items-baseline gap-3 py-2.5 border-t border-divider first:border-t-0">
      <span className="text-[13px] text-neutral-700 shrink-0">{label}</span>
      <span className="text-[15px] text-right truncate">{value}</span>
    </div>
  );
}

export const TransactionPreview = ({ operation, payloadStr }) => {
  if (!payloadStr) return <EmptyState title="No payload" message="There is no data to preview." />;
  let data;
  try {
    data = JSON.parse(payloadStr);
  } catch (e) {
    return <EmptyState title="Invalid data" message="The payload is not valid JSON." />;
  }

  if (operation.includes('VOUCHER')) {
    if (data.items) {
      // Purchase Item Invoice
      const subtotal = data.items.reduce((sum, item) => sum + (item.amount || 0), 0);
      const total = subtotal + (data.cgst || 0) + (data.sgst || 0) + (data.igst || 0) + (data.rounding_off || 0);
      return (
        <div className="flex flex-col">
          <Row label="Invoice no." value={data.invoice_number} />
          <Row label="Store" value={data.cost_center} />

          <p className="text-[10.5px] tracking-[0.1em] uppercase text-neutral-600 mt-3 mb-1">
            {data.items.length} item{data.items.length === 1 ? '' : 's'}
          </p>
          <div className="border-t border-divider">
            {data.items.map((item, idx) => (
              <div key={idx} className="flex justify-between gap-3 py-2.5 border-b border-divider">
                <div className="min-w-0">
                  <div className="text-[14px] truncate">{item.mapped_name || item.name}</div>
                  <div className="text-[12px] text-neutral-700">{item.qty} {item.uom} × {fmtMoney2(item.rate)}</div>
                </div>
                <div className="text-[14px] shrink-0">{fmtMoney2(item.amount)}</div>
              </div>
            ))}
          </div>

          <div className="flex flex-col gap-1 mt-3">
            <div className="flex justify-between text-sm text-neutral-700"><span>Subtotal</span><span>{fmtMoney2(subtotal)}</span></div>
            {(data.cgst > 0 || data.sgst > 0) && <div className="flex justify-between text-sm text-neutral-700"><span>CGST + SGST</span><span>{fmtMoney2(data.cgst + data.sgst)}</span></div>}
            {data.igst > 0 && <div className="flex justify-between text-sm text-neutral-700"><span>IGST</span><span>{fmtMoney2(data.igst)}</span></div>}
            {data.rounding_off !== 0 && <div className="flex justify-between text-sm text-neutral-700"><span>Rounding</span><span>{fmtMoney2(data.rounding_off)}</span></div>}
            <div className="flex justify-between items-baseline border-t border-text pt-2 mt-1">
              <span className="font-heading font-semibold text-base">Total</span>
              <span className="font-heading font-semibold text-lg">{fmtMoney2(total)}</span>
            </div>
          </div>
        </div>
      );
    } else if (data.ledger && data.amount !== undefined && !data.items) {
      // Sales Voucher
      return (
        <div className="flex flex-col">
          <Row label="Store" value={data.cost_center} />
          <Row label="Notes" value={data.narration} />
          <Row label="Recorded by" value={data.created_by} />
        </div>
      );
    } else if (data.supplier && data.invoice_number !== undefined && !data.items) {
      // Purchase Voucher (accounting mode -- no item breakdown)
      return (
        <div className="flex flex-col">
          <Row label="Invoice no." value={data.invoice_number} />
          <Row label="Store" value={data.cost_center} />
          <Row label="Notes" value={data.narration} />
        </div>
      );
    } else if (data.bank_ledger_name) {
      // Bank statement receipt/payment -- the bank ledger is the real
      // settlement account regardless of direction (debit_ledger/credit_ledger
      // swap depending on receipt vs payment).
      const isReceipt = data.debit_ledger === data.bank_ledger_name;
      return (
        <div className="flex flex-col">
          <Row label="Store" value={data.cost_center} />
          <Row label={isReceipt ? 'Deposited into' : 'Paid via'} value={data.bank_ledger_name} />
          <Row label="Notes" value={data.narration} />
        </div>
      );
    } else if ((data.debit_ledger && data.credit_ledger) || (data.from_account && data.to_account)) {
      // Payment or Transfer -- the title/amount are already shown in the
      // sheet's header (the party being paid), so only the settlement
      // account and any extra context are shown here.
      const credit = data.credit_ledger || data.from_account;
      return (
        <div className="flex flex-col">
          <Row label="Account" value={credit} />
          <Row label="Store" value={data.cost_center} />
          <Row label="Notes" value={data.narration} />
        </div>
      );
    } else if (data.from_store && data.to_store) {
      // Stock Transfer
      return (
        <div className="flex flex-col">
          <div className="flex items-center justify-between gap-3 py-2.5">
            <div className="min-w-0">
              <p className="text-[10.5px] tracking-[0.1em] uppercase text-neutral-600">From</p>
              <p className="text-[15px] truncate">{data.from_store}</p>
            </div>
            <MoveRight className="w-4 h-4 text-neutral-500 shrink-0" />
            <div className="min-w-0 text-right">
              <p className="text-[10.5px] tracking-[0.1em] uppercase text-neutral-600">To</p>
              <p className="text-[15px] truncate">{data.to_store}</p>
            </div>
          </div>
          <Row label="Item" value={data.item_name} />
          <Row label="Quantity" value={data.qty} />
          <Row label="Notes" value={data.narration} />
        </div>
      );
    } else if (Array.isArray(data)) {
      // Bank Statement multiple vouchers
      return (
        <div className="flex flex-col">
          <p className="text-[10.5px] tracking-[0.1em] uppercase text-neutral-600 mb-1">{data.length} bank transactions</p>
          <div className="border-t border-divider">
            {data.map((txn, idx) => (
              <div key={idx} className="flex justify-between gap-3 py-2.5 border-b border-divider">
                <div className="min-w-0">
                  <div className="text-[14px] truncate">{txn.target_ledger}</div>
                  <div className="text-[12px] text-neutral-700">{txn.date} · {txn.cost_center || 'Unallocated'}</div>
                </div>
                <div className="text-[14px] shrink-0">
                  {txn.withdrawal > 0 ? `− ${fmtMoney2(txn.withdrawal)}` : `${fmtMoney2(txn.deposit)}`}
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }
  }

  // Fallback
  return (
    <p className="text-sm text-neutral-700 text-center py-4">
      A preview isn&rsquo;t available for this operation type — use Raw data below.
    </p>
  );
};
