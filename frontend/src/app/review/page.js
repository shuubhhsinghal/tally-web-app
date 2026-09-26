'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { useUI } from '@/context/UIContext';
import { useSyncStatus } from '@/context/SyncStatusContext';
import { Wifi, WifiOff, Package, Landmark, ChevronRight, Trash2, Clock } from 'lucide-react';

function fmtMoney2(v) {
  const n = Number(v || 0);
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatInvoiceDate(dateStr) {
  if (!dateStr) return '-';
  const d = new Date(`${dateStr}T00:00:00`);
  if (isNaN(d)) return dateStr;
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export default function ReviewInbox() {
  const router = useRouter();
  const { showToast, showConfirmDialog } = useUI();
  const { isOnline } = useSyncStatus();
  const [drafts, setDrafts] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [queueCount, setQueueCount] = useState(null);

  const fetchDrafts = async (silent = false) => {
    try {
      if (!silent) setIsLoading(true);
      const [purchaseRes, bankRes] = await Promise.all([
        fetch('/api/purchase-drafts'),
        fetch('/api/bank-statement/drafts'),
      ]);
      if (!purchaseRes.ok) throw new Error('Failed to fetch drafts');
      const purchaseData = await purchaseRes.json();
      const bankData = bankRes.ok ? await bankRes.json() : { drafts: [] };
      const merged = [
        ...(purchaseData.drafts || []).map(d => ({ ...d, kind: 'purchase' })),
        ...(bankData.drafts || []).map(d => ({ ...d, kind: 'bank_statement' })),
      ].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
      setDrafts(merged);
    } catch (error) {
      if (!silent) showToast(error.message, 'error');
    } finally {
      if (!silent) setIsLoading(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line
    fetchDrafts();
    fetch('/api/dashboard/stats')
      .then(res => res.ok ? res.json() : null)
      .then(d => { if (d) setQueueCount(d.queue_count ?? 0); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const hasProcessing = drafts.some(d => d.status === 'PROCESSING');
    if (!hasProcessing) return;

    const intervalId = setInterval(() => {
      // eslint-disable-next-line
      fetchDrafts(true);
    }, 15000);

    return () => clearInterval(intervalId);
  }, [drafts]);

  const handleDeleteDraft = (e, draft) => {
    e.stopPropagation();
    const isBank = draft.kind === 'bank_statement';
    showConfirmDialog({
      title: isBank ? 'Delete this statement?' : 'Delete this failed draft?',
      message: isBank
        ? 'This removes the analyzed statement from Review. Nothing has been sent to Tally.'
        : 'This will remove the draft from Review. It will not affect Tally or accounting data.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      confirmColor: 'bg-red-600 hover:bg-red-700',
      onConfirm: async () => {
        try {
          const url = isBank ? `/api/bank-statement/drafts/${draft.id}` : `/api/purchase-drafts/${draft.id}`;
          const res = await fetch(url, { method: 'DELETE' });
          if (!res.ok) throw new Error('Failed to delete draft');
          showToast('Deleted successfully', 'success');
          fetchDrafts(true);
        } catch (error) {
          showToast(error.message, 'error');
        }
      }
    });
  };

  const formatDate = (isoString) => {
    if (!isoString) return '';
    try {
      // The backend stores this as a naive datetime.now().isoformat() string
      // with no timezone designator, and its server clock runs in UTC -- JS's
      // Date constructor interprets a Z-less ISO string as LOCAL time, not
      // UTC, so without this it silently shows the wrong time (off by
      // whatever the server-vs-viewer UTC offset is, e.g. 5:30 for IST).
      const date = new Date(isoString + (isoString.includes('Z') ? '' : 'Z'));
      return date.toLocaleDateString('en-IN', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      });
    } catch {
      return isoString;
    }
  };

  const kicker = `${drafts.length} DRAFT${drafts.length === 1 ? '' : 'S'} TO REVIEW`;

  return (
    <div className="min-h-screen bg-bg pb-24">
      <TopBar
        title="Review"
        kicker={kicker}
        rightContent={
          <div className={`flex items-center gap-1.5 h-9 px-3 rounded-full border shrink-0 ${isOnline === false ? 'border-accent text-accent-700' : 'border-divider text-neutral-700'}`}>
            {isOnline === false ? <WifiOff className="w-3.5 h-3.5" /> : <Wifi className="w-3.5 h-3.5" />}
            <span className="text-xs font-medium whitespace-nowrap">
              {isOnline === false ? 'Offline' : 'Online'}{queueCount > 0 ? ` · ${queueCount} saved` : ''}
            </span>
          </div>
        }
      />

      <div className="max-w-md mx-auto px-4 pt-6 space-y-4">
        <div className="flex justify-between items-baseline gap-3">
          <h2 className="font-heading font-semibold text-xl">Pending review</h2>
          <span className="shrink-0 text-[13px] font-medium text-accent-700 bg-accent/12 px-3 py-1 rounded-full">
            {drafts.length} draft{drafts.length === 1 ? '' : 's'}
          </span>
        </div>
        <p className="text-[13px] text-neutral-700 -mt-2">
          Invoices and bank statements waiting for a check before they&apos;re pushed to Tally.
        </p>

        {isLoading ? (
          <p className="text-sm text-neutral-600 text-center py-16">Loading&hellip;</p>
        ) : drafts.length === 0 ? (
          <EmptyState
            title="All caught up"
            message="There is nothing pending review right now."
            icon={Clock}
          />
        ) : (
          <div className="flex flex-col gap-3">
            {drafts.map((draft) => {
              if (draft.kind === 'bank_statement') {
                const isProcessing = draft.status === 'PROCESSING';
                const isFailed = draft.status === 'FAILED';
                const clickable = !isProcessing && !isFailed;
                const needsReview = draft.unmapped_count > 0;
                return (
                  <Card
                    key={`bank-${draft.id}`}
                    onClick={clickable ? () => router.push(`/bank-statement?draftId=${draft.id}`) : undefined}
                    className="flex flex-col gap-3"
                  >
                    <div className="flex justify-between items-start gap-3 pb-3 border-b border-divider">
                      <div className="min-w-0">
                        <p className="font-heading font-semibold text-lg leading-tight truncate">{draft.bank_ledger_name}</p>
                        <p className="text-[13px] text-neutral-700 mt-0.5">Bank statement</p>
                      </div>
                      {!isProcessing && (
                        <div className="text-right shrink-0">
                          <p className="text-[10px] tracking-[0.1em] uppercase text-neutral-600">Transactions</p>
                          <p className="text-[15px] mt-0.5">{draft.transaction_count}</p>
                        </div>
                      )}
                    </div>

                    {isFailed ? (
                      <p className="text-[13px] text-accent-700 truncate">{draft.error_message || 'Something went wrong reading this statement.'}</p>
                    ) : (
                      <div className="flex items-center gap-1.5 text-[13px] text-neutral-700 min-w-0">
                        <Landmark className="w-3.5 h-3.5 shrink-0" />
                        <span className="truncate">
                          {isProcessing
                            ? 'Reading this statement now'
                            : `${draft.transaction_count} transaction${draft.transaction_count === 1 ? '' : 's'}${needsReview ? ` · ${draft.unmapped_count} to map` : ''}`}
                        </span>
                      </div>
                    )}

                    <div className="flex items-center gap-2">
                      {isProcessing ? (
                        <span className="inline-flex items-center gap-2 text-[12.5px] text-accent-700 border border-accent rounded-full px-3 py-1">
                          <span className="w-3 h-3 rounded-full border-2 border-accent/30 border-t-accent-700 animate-spin" />
                          Analyzing&hellip;
                        </span>
                      ) : isFailed ? (
                        <span className="text-[12.5px] text-accent-700 border border-accent rounded-full px-3 py-1">Analysis failed</span>
                      ) : (
                        <span className="text-[12.5px] text-accent-700 border border-accent rounded-full px-3 py-1">
                          {needsReview ? 'Needs mapping' : 'Ready to push'}
                        </span>
                      )}
                      <button
                        onClick={(e) => handleDeleteDraft(e, draft)}
                        className="ml-auto p-1.5 text-red-600 hover:bg-red-50 rounded-full transition-colors"
                        aria-label="Delete draft"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    <div className="flex justify-between items-center">
                      <span className="text-[11.5px] text-neutral-600">Created {formatDate(draft.created_at)}</span>
                      {clickable && (
                        <span className="flex items-center gap-0.5 text-[13px] text-accent-700">
                          Open <ChevronRight className="w-3.5 h-3.5" />
                        </span>
                      )}
                    </div>
                  </Card>
                );
              }

              const clickable = draft.status !== 'PROCESSING';
              const needsReview = draft.unmatched_count > 0;
              return (
                <Card
                  key={`purchase-${draft.id}`}
                  onClick={clickable ? () => router.push(`/purchase?draftId=${draft.id}`) : undefined}
                  className="flex flex-col gap-3"
                >
                  <div className="flex justify-between items-start gap-3 pb-3 border-b border-divider">
                    <div className="min-w-0">
                      <p className="font-heading font-semibold text-lg leading-tight truncate">{draft.supplier_name || 'Unknown supplier'}</p>
                      <p className="text-[13px] text-neutral-700 mt-0.5 truncate">{draft.invoice_number || 'No invoice number'}</p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-[10px] tracking-[0.1em] uppercase text-neutral-600">Invoice date</p>
                      <p className="text-[15px] mt-0.5 whitespace-nowrap">{formatInvoiceDate(draft.invoice_date)}</p>
                    </div>
                  </div>

                  <div className="flex justify-between items-center gap-3">
                    <div className="flex items-center gap-1.5 text-[13px] text-neutral-700 min-w-0">
                      <Package className="w-3.5 h-3.5 shrink-0" />
                      <span className="truncate">
                        {draft.item_count} item{draft.item_count === 1 ? '' : 's'}
                        {needsReview ? ` · ${draft.unmatched_count} not matched` : ''}
                      </span>
                    </div>
                    <p className="font-heading font-semibold text-lg whitespace-nowrap">{fmtMoney2(draft.grand_total)}</p>
                  </div>

                  <div>
                    {draft.status === 'PROCESSING' ? (
                      <span className="inline-flex items-center gap-2 text-[12.5px] text-accent-700 border border-accent rounded-full px-3 py-1">
                        <span className="w-3 h-3 rounded-full border-2 border-accent/30 border-t-accent-700 animate-spin" />
                        Parsing&hellip;
                      </span>
                    ) : draft.status === 'FAILED' ? (
                      <div className="flex items-center gap-2">
                        <span className="text-[12.5px] text-accent-700 border border-accent rounded-full px-3 py-1">Parsing failed</span>
                        <button
                          onClick={(e) => handleDeleteDraft(e, draft)}
                          className="p-1.5 text-red-600 hover:bg-red-50 rounded-full transition-colors"
                          aria-label="Delete draft"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    ) : (
                      <span className="text-[12.5px] text-accent-700 border border-accent rounded-full px-3 py-1">
                        {needsReview ? 'Review required' : 'Ready to push'}
                      </span>
                    )}
                  </div>

                  <div className="flex justify-between items-center">
                    <span className="text-[11.5px] text-neutral-600">Created {formatDate(draft.created_at)}</span>
                    {clickable && (
                      <span className="flex items-center gap-0.5 text-[13px] text-accent-700">
                        Open <ChevronRight className="w-3.5 h-3.5" />
                      </span>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
