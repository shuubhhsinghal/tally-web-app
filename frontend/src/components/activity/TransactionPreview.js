'use client';

import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { MoveRight } from 'lucide-react';

export const TransactionPreview = ({ operation, payloadStr }) => {
  if (!payloadStr) return <EmptyState title="No Payload" message="There is no data to preview." />;
  let data;
  try {
    data = JSON.parse(payloadStr);
  } catch (e) {
    return <EmptyState title="Invalid Data" message="The payload is not valid JSON." />;
  }

  if (operation.includes('VOUCHER')) {
    if (data.items) {
      // Purchase Item Invoice
      const subtotal = data.items.reduce((sum, item) => sum + (item.amount || 0), 0);
      const total = subtotal + (data.cgst || 0) + (data.sgst || 0) + (data.igst || 0) + (data.rounding_off || 0);
      return (
        <div className="space-y-4">
          <Card className="flex flex-col gap-3">
            <div className="flex justify-between items-start border-b border-gray-100 dark:border-gray-800 pb-3">
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase">Supplier</p>
                <p className="text-sm font-bold text-gray-900 dark:text-white">{data.supplier || data.name}</p>
              </div>
              <div className="text-right">
                <p className="text-xs font-bold text-gray-400 uppercase">Date</p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.date || data.tally_date}</p>
              </div>
            </div>
            <div className="flex justify-between items-start">
              <div>
                <p className="text-xs font-bold text-gray-400 uppercase">Inv No</p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.invoice_number}</p>
              </div>
              <div className="text-right">
                <p className="text-xs font-bold text-gray-400 uppercase">Cost Center</p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.cost_center}</p>
              </div>
            </div>
          </Card>

          <h3 className="text-xs font-bold text-gray-500 uppercase px-1">{data.items.length} Line Items</h3>
          <div className="space-y-2">
            {data.items.map((item, idx) => (
              <Card key={idx} className="p-3">
                <div className="flex justify-between items-start">
                  <p className="text-sm font-bold text-gray-900 dark:text-white">{item.mapped_name || item.name}</p>
                  <p className="text-sm font-bold text-gray-900 dark:text-white">₹ {item.amount?.toFixed(2)}</p>
                </div>
                <p className="text-xs font-medium text-gray-500 mt-1">{item.qty} {item.uom} × ₹{item.rate}</p>
              </Card>
            ))}
          </div>

          <Card className="flex flex-col gap-2">
            <div className="flex justify-between text-xs text-gray-500 font-medium"><span>Subtotal</span><span>₹ {subtotal.toFixed(2)}</span></div>
            {(data.cgst > 0 || data.sgst > 0) && <div className="flex justify-between text-xs text-gray-500 font-medium"><span>CGST + SGST</span><span>₹ {(data.cgst + data.sgst).toFixed(2)}</span></div>}
            {data.igst > 0 && <div className="flex justify-between text-xs text-gray-500 font-medium"><span>IGST</span><span>₹ {data.igst.toFixed(2)}</span></div>}
            {data.rounding_off !== 0 && <div className="flex justify-between text-xs text-gray-500 font-medium"><span>Rounding</span><span>{data.rounding_off}</span></div>}
            <div className="h-px bg-gray-100 dark:bg-gray-800 my-1" />
            <div className="flex justify-between text-lg font-black text-gray-900 dark:text-white"><span>Total</span><span>₹ {total.toFixed(2)}</span></div>
          </Card>
        </div>
      );
    } else if (data.ledger && data.amount !== undefined && !data.items) {
      // Sales Voucher
      return (
        <Card className="flex flex-col gap-3">
          <div className="flex justify-between items-start border-b border-gray-100 dark:border-gray-800 pb-3">
            <div><p className="text-xs font-bold text-gray-400 uppercase">Customer</p><p className="text-sm font-bold text-gray-900 dark:text-white">{data.ledger}</p></div>
            <div className="text-right"><p className="text-xs font-bold text-gray-400 uppercase">Amount</p><p className="text-lg font-black text-teal-600">₹ {Number(data.amount).toFixed(2)}</p></div>
          </div>
          {data.cost_center && <div><p className="text-xs font-bold text-gray-400 uppercase">Cost Center</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.cost_center}</p></div>}
          {data.narration && <div><p className="text-xs font-bold text-gray-400 uppercase">Narration</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.narration}</p></div>}
        </Card>
      );
    } else if ((data.debit_ledger && data.credit_ledger) || (data.from_account && data.to_account)) {
      // Payment or Transfer
      const debit = data.debit_ledger || data.to_account;
      const credit = data.credit_ledger || data.from_account;
      return (
        <Card className="flex flex-col gap-3">
          <div className="flex justify-between items-start border-b border-gray-100 dark:border-gray-800 pb-3">
            <div><p className="text-xs font-bold text-gray-400 uppercase">Amount</p><p className="text-2xl font-black text-teal-600">₹ {Number(data.amount).toFixed(2)}</p></div>
            <div className="text-right"><p className="text-xs font-bold text-gray-400 uppercase">Date</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.date || 'Today'}</p></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><p className="text-xs font-bold text-gray-400 uppercase">Debit (To)</p><p className="text-sm font-bold text-gray-900 dark:text-white">{debit}</p></div>
            <div><p className="text-xs font-bold text-gray-400 uppercase">Credit (From)</p><p className="text-sm font-bold text-gray-900 dark:text-white">{credit}</p></div>
          </div>
          {data.cost_center && <div><p className="text-xs font-bold text-gray-400 uppercase">Cost Center</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.cost_center}</p></div>}
          {data.narration && <div><p className="text-xs font-bold text-gray-400 uppercase">Narration</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.narration}</p></div>}
        </Card>
      );
    } else if (data.from_store && data.to_store) {
      // Stock Transfer
      return (
        <Card className="flex flex-col gap-3">
          <div className="flex justify-between items-center border-b border-gray-100 dark:border-gray-800 pb-3">
            <div className="flex-1"><p className="text-xs font-bold text-gray-400 uppercase">From</p><p className="text-sm font-bold text-gray-900 dark:text-white">{data.from_store}</p></div>
            <MoveRight className="w-5 h-5 text-gray-400 mx-2" />
            <div className="flex-1 text-right"><p className="text-xs font-bold text-gray-400 uppercase">To</p><p className="text-sm font-bold text-gray-900 dark:text-white">{data.to_store}</p></div>
          </div>
          <div><p className="text-xs font-bold text-gray-400 uppercase">Item</p><p className="text-sm font-bold text-gray-900 dark:text-white">{data.item_name}</p></div>
          <div className="grid grid-cols-2 gap-4">
            <div><p className="text-xs font-bold text-gray-400 uppercase">Quantity</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.qty}</p></div>
            <div><p className="text-xs font-bold text-gray-400 uppercase">Date</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.tally_date || 'Today'}</p></div>
          </div>
          {data.narration && <div><p className="text-xs font-bold text-gray-400 uppercase">Narration</p><p className="text-sm font-medium text-gray-900 dark:text-gray-300">{data.narration}</p></div>}
        </Card>
      );
    } else if (Array.isArray(data)) {
      // Bank Statement multiple vouchers
      return (
        <div className="space-y-4">
          <h3 className="text-xs font-bold text-gray-500 uppercase px-1">{data.length} Bank Transactions</h3>
          <div className="space-y-2">
            {data.map((txn, idx) => (
              <Card key={idx} className="p-3 flex flex-col gap-2">
                <div className="flex justify-between">
                  <p className="text-sm font-bold text-gray-900 dark:text-white">{txn.target_ledger}</p>
                  <p className={`text-sm font-bold ${txn.withdrawal > 0 ? 'text-red-600' : 'text-green-600'}`}>
                    {txn.withdrawal > 0 ? `- ₹${txn.withdrawal}` : `+ ₹${txn.deposit}`}
                  </p>
                </div>
                <div className="flex justify-between text-xs text-gray-500">
                  <span>{txn.date}</span>
                  <span>{txn.cost_center || 'No CC'}</span>
                </div>
              </Card>
            ))}
          </div>
        </div>
      );
    }
  }

  // Fallback
  return (
    <Card className="flex flex-col gap-2">
      <p className="text-sm font-medium text-gray-700 dark:text-gray-300 text-center py-4">
        Preview not available for this operation type.<br />Please use the Raw Data view.
      </p>
    </Card>
  );
};
