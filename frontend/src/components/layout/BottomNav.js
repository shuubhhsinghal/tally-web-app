'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Home, Plus, Landmark, ListChecks, ShoppingBag, ShoppingCart, CreditCard, ArrowRightLeft, ArrowRight, Inbox, BarChart3, Package } from 'lucide-react';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';

export default function BottomNav() {
  const pathname = usePathname();
  const router = useRouter();
  const { showActionSheet } = useUI();
  const { user } = useAuth();

  // Highlight 'home' if on dashboard, 'bank' if on bank-statement, 'queue' if on queue
  const isHome = pathname === '/dashboard' || pathname === '/';
  const isBank = pathname.startsWith('/bank-statement');
  const isReview = pathname.startsWith('/review');
  const isReports = pathname.startsWith('/reporting');
  const isQueue = pathname.startsWith('/queue');

  const handleNewEntry = () => {
    showActionSheet({
      title: 'New Entry',
      options: [
        { label: 'Record a sale', icon: <ShoppingCart className="w-5 h-5" />, colorClass: 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400', onClick: () => router.push('/sales') },
        { label: 'Record a purchase', icon: <ShoppingBag className="w-5 h-5" />, colorClass: 'bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400', onClick: () => router.push('/purchase') },
        { label: 'Repack / Pack Stock', icon: <Package className="w-5 h-5" />, colorClass: 'bg-rose-100 text-rose-600 dark:bg-rose-900/30 dark:text-rose-400', onClick: () => router.push('/repack') },
        { label: 'Record a payment', icon: <CreditCard className="w-5 h-5" />, colorClass: 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400', onClick: () => router.push('/payment') },
        { label: 'Move money between accounts', icon: <ArrowRightLeft className="w-5 h-5" />, colorClass: 'bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400', onClick: () => router.push('/transfer') },
        { label: 'Move stock between stores', icon: <ArrowRight className="w-5 h-5" />, colorClass: 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400', onClick: () => router.push('/stock-transfer') },
        // Loans entry point removed while the feature is being redesigned --
        // the route/code/data are all still intact, just not reachable from
        // the UI. See /loans directly to still access the old build.
      ]
    });
  };

  return (
    <>
      {/* Spacer to prevent content from hiding behind the fixed nav */}
      <div className="h-20" />
      
      <div className="fixed bottom-0 left-0 right-0 z-40 bg-white/90 dark:bg-gray-900/90 backdrop-blur-md border-t border-gray-100 dark:border-gray-800 pb-safe">
        <div className="max-w-md mx-auto flex items-center justify-around h-16 px-2">
          
          <Link href="/dashboard" className="flex flex-col items-center justify-center w-16 h-full gap-1">
            <Home className={`w-6 h-6 ${isHome ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`} />
            <span className={`text-[10px] font-medium ${isHome ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`}>Home</span>
          </Link>

          {/* Plus Button - Elevated */}
          <div className="relative -top-4 flex flex-col items-center justify-center">
            <button 
              onClick={handleNewEntry}
              className="w-14 h-14 bg-teal-600 hover:bg-teal-700 active:scale-95 text-white rounded-full flex items-center justify-center shadow-lg shadow-teal-600/30 transition-all"
            >
              <Plus className="w-8 h-8" />
            </button>
          </div>

          <Link href="/bank-statement" className="flex flex-col items-center justify-center w-16 h-full gap-1">
            <Landmark className={`w-6 h-6 ${isBank ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`} />
            <span className={`text-[10px] font-medium ${isBank ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`}>Bank</span>
          </Link>

          <Link href="/reporting" className="flex flex-col items-center justify-center w-16 h-full gap-1">
            <BarChart3 className={`w-6 h-6 ${isReports ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`} />
            <span className={`text-[10px] font-medium ${isReports ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`}>Reports</span>
          </Link>

          <Link href="/review" className="flex flex-col items-center justify-center w-16 h-full gap-1">
            <Inbox className={`w-6 h-6 ${isReview ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`} />
            <span className={`text-[10px] font-medium ${isReview ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`}>Review</span>
          </Link>

          <Link href="/queue" className="flex flex-col items-center justify-center w-16 h-full gap-1">
            <ListChecks className={`w-6 h-6 ${isQueue ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`} />
            <span className={`text-[10px] font-medium ${isQueue ? 'text-teal-600 dark:text-teal-400' : 'text-gray-400 dark:text-gray-500'}`}>Queue</span>
          </Link>

        </div>
      </div>
    </>
  );
}
