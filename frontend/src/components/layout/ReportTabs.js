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
    <div className="w-full px-4 pt-4">
      <div
        className="grid border border-divider rounded-md overflow-hidden max-w-md mx-auto"
        style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
      >
        {tabs.map((tab) => {
          const isActive = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
          return (
            <Link
              key={tab.name}
              href={tab.href}
              className={`h-11 flex items-center justify-center text-[13px] whitespace-nowrap transition-colors ${
                isActive ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'
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
