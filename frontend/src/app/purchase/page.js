'use client';
import React, { useState } from "react";
import TopBar from '@/components/layout/TopBar';
import { ItemWiseMode } from './ItemWiseMode';
import { AccountingMode } from './AccountingMode';
import { PurchaseReturnMode } from './PurchaseReturnMode';
import { useRouter, useSearchParams } from 'next/navigation';

export default function PurchaseVoucher() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Lets the "Purchase return" bottom-nav tile deep-link straight into the
  // Return tab (/purchase?mode=return) instead of landing on Item-wise and
  // making the user click over.
  const initialMode = searchParams.get('mode') === 'return' ? 'return' : 'item_wise';
  const [mode, setMode] = useState(initialMode); // 'accounting', 'item_wise', or 'return'

  return (
    <div className="min-h-screen bg-bg pb-20">
      <TopBar
        title="Record a purchase"
        showBack
        rightContent={
          <div className="text-[11px] px-2.5 py-1 border border-divider rounded text-neutral-700 shrink-0">
            Purchase voucher
          </div>
        }
      />

      <div className="max-w-md mx-auto p-4 mt-4 space-y-4">

        {/* Segmented Control */}
        <div className="grid grid-cols-3 border border-divider rounded-md overflow-hidden w-full">
          <button
            type="button"
            onClick={() => setMode("item_wise")}
            className={`py-2.5 text-[15px] font-heading font-semibold transition-colors ${
              mode === "item_wise" ? "border border-accent text-accent-700 bg-accent/8 -m-px" : "text-text hover:bg-text/5"
            }`}
          >
            Item wise
          </button>
          <button
            type="button"
            onClick={() => setMode("accounting")}
            className={`py-2.5 text-[15px] font-heading font-semibold transition-colors ${
              mode === "accounting" ? "border border-accent text-accent-700 bg-accent/8 -m-px" : "text-text hover:bg-text/5"
            }`}
          >
            Accounting
          </button>
          <button
            type="button"
            onClick={() => setMode("return")}
            className={`py-2.5 text-[15px] font-heading font-semibold transition-colors ${
              mode === "return" ? "border border-accent text-accent-700 bg-accent/8 -m-px" : "text-text hover:bg-text/5"
            }`}
          >
            Return
          </button>
        </div>

        {mode !== "return" && (
          <p className="text-neutral-700 text-[15px] leading-relaxed">
            Photograph the supplier&apos;s bill and the items are read for you — or type them in yourself.
          </p>
        )}

        <div className="mt-6">
          {mode === "accounting" ? (
            <AccountingMode onPostSuccess={() => router.push('/dashboard')} />
          ) : mode === "return" ? (
            <PurchaseReturnMode onPostSuccess={() => router.push('/dashboard')} />
          ) : (
            <React.Suspense fallback={<div className="p-8 text-center text-neutral-700">Loading editor...</div>}>
              <ItemWiseMode onPostSuccess={() => router.push('/review')} />
            </React.Suspense>
          )}
        </div>

      </div>
    </div>
  );
}
