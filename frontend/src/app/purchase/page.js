'use client';
import React, { useState } from "react";
import TopBar from '@/components/layout/TopBar';
import { ItemWiseMode } from './ItemWiseMode';
import { AccountingMode } from './AccountingMode';
import { useRouter } from 'next/navigation';

export default function PurchaseVoucher() {
  const router = useRouter();
  const [mode, setMode] = useState("item_wise"); // 'accounting' or 'item_wise'

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar title="Record a purchase" showBack />
      
      <div className="max-w-md mx-auto p-4 mt-4 space-y-6">
        
        {/* Segmented Control */}
        <div className="flex bg-gray-200 dark:bg-gray-800 p-1 rounded-xl w-full">
          <button
            type="button"
            onClick={() => setMode("item_wise")}
            className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-colors ${
              mode === "item_wise" ? "bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white" : "text-gray-500 dark:text-gray-400"
            }`}
          >
            Item Wise
          </button>
          <button
            type="button"
            onClick={() => setMode("accounting")}
            className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-colors ${
              mode === "accounting" ? "bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white" : "text-gray-500 dark:text-gray-400"
            }`}
          >
            Accounting
          </button>
        </div>

        <div className="mt-6">
          {mode === "accounting" ? (
            <AccountingMode onPostSuccess={() => router.push('/dashboard')} />
          ) : (
            <React.Suspense fallback={<div className="p-8 text-center text-gray-500">Loading editor...</div>}>
              <ItemWiseMode onPostSuccess={() => router.push('/review')} />
            </React.Suspense>
          )}
        </div>

      </div>
    </div>
  );
}
