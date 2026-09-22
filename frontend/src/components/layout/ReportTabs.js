'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';

export default function ReportTabs() {
  const pathname = usePathname();
  const { user } = useAuth();
  const isOwner = !user || user.is_owner;

  const tabs = [
    { name: 'Sales', href: '/reporting/sales' },
    { name: 'Purchases', href: '/reporting/purchases' },
    ...(isOwner ? [{ name: 'Creditors', href: '/reporting/creditors' }] : []),
    { name: 'Daybook', href: '/reporting/daybook' },
    { name: 'P&L', href: '/reporting/pl' },
  ];

  return (
    <div className="w-full bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 sticky top-14 z-20 overflow-x-auto hide-scrollbar">
      <div className="flex px-4 max-w-7xl mx-auto space-x-1 min-w-max">
        {tabs.map((tab) => {
          const isActive = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
          return (
            <Link
              key={tab.name}
              href={tab.href}
              className={`px-4 py-3 text-sm font-medium whitespace-nowrap transition-colors border-b-2 ${
                isActive
                  ? 'border-blue-600 text-blue-600 dark:border-blue-500 dark:text-blue-400'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
              }`}
            >
              {tab.name}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
