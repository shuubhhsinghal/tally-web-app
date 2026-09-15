'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { useUI } from '@/context/UIContext';
import { Clock, ExternalLink, Package, Trash2 } from 'lucide-react';

export default function ReviewInbox() {
  const router = useRouter();
  const { showToast, showConfirmDialog } = useUI();
  const [drafts, setDrafts] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchDrafts = async (silent = false) => {
    try {
      if (!silent) setIsLoading(true);
      const res = await fetch('/api/purchase-drafts');
      if (!res.ok) throw new Error('Failed to fetch drafts');
      const data = await res.json();
      setDrafts(data.drafts || []);
    } catch (error) {
      if (!silent) showToast(error.message, 'error');
    } finally {
      if (!silent) setIsLoading(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line
    fetchDrafts();
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

  const handleDeleteDraft = (e, draftId) => {
    e.stopPropagation();
    showConfirmDialog({
      title: 'Delete this failed draft?',
      message: 'This will remove the draft from Review. It will not affect Tally or accounting data.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      confirmColor: 'bg-red-600 hover:bg-red-700',
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/purchase-drafts/${draftId}`, { method: 'DELETE' });
          if (!res.ok) throw new Error('Failed to delete draft');
          showToast('Draft deleted successfully', 'success');
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
      const date = new Date(isoString);
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

  return (
    <div className="flex flex-col min-h-[100dvh]">
      <TopBar title="Review Inbox" onBack={() => router.push('/dashboard')} />
      
      <div className="flex-1 p-4 pb-24 overflow-y-auto w-full max-w-md mx-auto space-y-4">
        <div className="flex justify-between items-center mb-2 px-1">
          <h2 className="text-xl font-black text-gray-900 dark:text-white tracking-tight">Pending Review</h2>
          <span className="text-xs font-bold text-teal-600 bg-teal-50 dark:bg-teal-900/30 px-2 py-1 rounded-full">
            {drafts.length} {drafts.length === 1 ? 'Draft' : 'Drafts'}
          </span>
        </div>

        {isLoading ? (
          <div className="flex flex-col items-center justify-center p-8 space-y-4 animate-pulse">
            <div className="w-12 h-12 rounded-full border-4 border-teal-100 border-t-teal-600 animate-spin" />
            <p className="text-sm font-medium text-gray-400">Loading drafts...</p>
          </div>
        ) : drafts.length === 0 ? (
          <EmptyState 
            title="All Caught Up!" 
            message="There are no pending invoices waiting for review." 
            icon={Clock}
          />
        ) : (
          <div className="space-y-4">
            {drafts.map((draft) => (
              <Card 
                key={draft.id} 
                className={`p-0 overflow-hidden flex flex-col transition-colors ${
                  draft.status === 'PROCESSING' ? 'opacity-80' : 'cursor-pointer hover:border-teal-500/50'
                }`}
                onClick={() => {
                  if (draft.status !== 'PROCESSING') {
                    router.push(`/purchase?draftId=${draft.id}`);
                  }
                }}
              >
                <div className="p-4 flex flex-col gap-3">
                  <div className="flex justify-between items-start border-b border-gray-100 dark:border-gray-800 pb-3">
                    <div className="flex-1">
                      <p className="text-sm font-black text-gray-900 dark:text-white leading-tight line-clamp-1">{draft.supplier_name}</p>
                      <p className="text-xs font-bold text-gray-400 uppercase mt-0.5">{draft.invoice_number || 'No Invoice #'}</p>
                    </div>
                    <div className="text-right pl-2">
                      <p className="text-sm font-black text-teal-600 dark:text-teal-400 whitespace-nowrap">₹ {(draft.grand_total || 0).toFixed(2)}</p>
                      <p className="text-xs font-bold text-gray-400 uppercase mt-0.5">{draft.invoice_date || '-'}</p>
                    </div>
                  </div>
                  
                  <div className="flex justify-between items-center">
                    <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500">
                      <Package className="w-3.5 h-3.5" />
                      <span>{draft.item_count} {draft.item_count === 1 ? 'Item' : 'Items'}</span>
                    </div>
                    
                    {draft.status === 'PROCESSING' ? (
                      <span className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-blue-600 bg-blue-50 dark:bg-blue-900/30 px-2 py-1 rounded">
                        <div className="w-3 h-3 rounded-full border-2 border-blue-200 border-t-blue-600 animate-spin" />
                        Parsing...
                      </span>
                    ) : draft.status === 'FAILED' ? (
                      <div className="flex items-center gap-2">
                        <span className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-red-600 bg-red-50 dark:bg-red-900/30 px-2 py-1 rounded">
                          Parsing Failed
                        </span>
                        <button
                          onClick={(e) => handleDeleteDraft(e, draft.id)}
                          className="p-1.5 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-full transition-colors"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    ) : (
                      <span className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-amber-600 bg-amber-50 dark:bg-amber-900/30 px-2 py-1 rounded">
                        Review Required
                      </span>
                    )}
                  </div>
                </div>
                
                <div className="bg-gray-50 dark:bg-gray-800/50 px-4 py-2 border-t border-gray-100 dark:border-gray-800 flex justify-between items-center">
                  <span className="text-[10px] font-medium text-gray-400">
                    Created {formatDate(draft.created_at)}
                  </span>
                  
                  {draft.status !== 'PROCESSING' && (
                    <div className="flex items-center gap-1 text-teal-600 dark:text-teal-400 text-xs font-bold uppercase tracking-wider">
                      Open
                      <ExternalLink className="w-3.5 h-3.5" />
                    </div>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
