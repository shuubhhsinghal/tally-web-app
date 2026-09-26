'use client';

import React, { useState, useEffect } from 'react';
import TopBar from '@/components/layout/TopBar';
import { EmptyState } from '@/components/ui/EmptyState';
import { ShoppingCart, ShoppingBag, CreditCard, ArrowRightLeft } from 'lucide-react';
import Link from 'next/link';
import { ActivityRow } from '@/components/activity/ActivityRow';

export default function Dashboard() {
  const [stats, setStats] = useState({ queue_count: 0, cache_count: 0, failed_count: 0, today_sales: 0, today_sales_pending_count: 0, tally_online: false });
  const [activities, setActivities] = useState([]);
  const [mounted, setMounted] = useState(false);

  const fetchStats = async () => {
    try {
      // Independent endpoints -- fetch in parallel instead of waiting for
      // stats to fully resolve before even starting the activity request.
      const [res, actRes] = await Promise.all([
        fetch('/api/dashboard/stats'),
        fetch('/api/dashboard/activity'),
      ]);

      if (res.ok) {
        const data = await res.json();
        setStats(data);
        localStorage.setItem('dash_stats', JSON.stringify(data));
      }

      if (actRes.ok) {
        const data = await actRes.json();
        setActivities(data);
        localStorage.setItem('dash_activity', JSON.stringify(data));
      }
    } catch (e) {
      console.error("Failed to load dashboard data", e);
    }
  };

  useEffect(() => {
    setMounted(true);

    // Load cached data instantly (0ms) so the page is not blank
    try {
      const cachedStats = localStorage.getItem('dash_stats');
      const cachedActivity = localStorage.getItem('dash_activity');
      if (cachedStats) setStats(JSON.parse(cachedStats));
      if (cachedActivity) setActivities(JSON.parse(cachedActivity));
    } catch { }

    // Then refresh from backend in background
    fetchStats();
    const interval = setInterval(fetchStats, 5000);
    return () => clearInterval(interval);
  }, []);

  if (!mounted) return null;

  const todayKicker = new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' }).toUpperCase();

  return (
    <div className="min-h-screen bg-bg pb-20">
      <TopBar title="Mom's Pride" kicker={todayKicker} />

      <div className="max-w-md mx-auto px-5 pt-7 space-y-7">

        {/* Hero: Today's Sales -- the number a shop owner actually cares
            about, as a plain typographic figure matching the design
            system's Home screen (no card, no gradient). */}
        <div>
          <p className="text-[10.5px] tracking-[0.12em] uppercase text-accent-700">Today&apos;s sales</p>
          <div className="font-heading flex items-baseline gap-1 mt-1.5 [font-feature-settings:'tnum']">
            <span className="text-3xl text-neutral-600">₹</span>
            <span className="text-6xl leading-none tracking-tight">{Math.round(stats.today_sales || 0).toLocaleString('en-IN')}</span>
          </div>
          {stats.today_sales_pending_count > 0 && (
            <p className="text-[13px] text-neutral-700 mt-2">
              <span className="text-accent-700">{stats.today_sales_pending_count} sale{stats.today_sales_pending_count === 1 ? '' : 's'}</span> still syncing to Tally
            </p>
          )}
        </div>

        {/* Queue health -- a quiet divided strip, only drawing the eye
            (via the accent color) when something needs attention. Uses the
            three real figures the dashboard stats endpoint has -- "Stock"
            in place of the mockup's "To collect" (receivables), since
            there's no accounts-receivable summary wired up yet. */}
        <div className="grid grid-cols-3 border-t border-b border-divider">
          <Link href="/queue?status=PENDING" className="py-3.5 pr-3 flex flex-col gap-0.5 hover:bg-text/4 transition-colors">
            <span className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Sync queue</span>
            <span className="font-heading text-2xl leading-tight [font-feature-settings:'tnum']">{stats.queue_count}</span>
            <span className="text-[11.5px] text-neutral-700">waiting to sync</span>
          </Link>
          <Link href="/masters" className="py-3.5 px-3 border-l border-divider flex flex-col gap-0.5 hover:bg-text/4 transition-colors">
            <span className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Stock</span>
            <span className="font-heading text-2xl leading-tight [font-feature-settings:'tnum']">{stats.cache_count}</span>
            <span className="text-[11.5px] text-neutral-700">items</span>
          </Link>
          <Link href="/queue?status=FAILED" className="py-3.5 pl-3 border-l border-divider flex flex-col gap-0.5 hover:bg-text/4 transition-colors">
            <span className="text-[10px] tracking-[0.1em] uppercase text-neutral-700">Failed</span>
            <span className={`font-heading text-2xl leading-tight [font-feature-settings:'tnum'] ${stats.failed_count > 0 ? 'text-accent-700' : ''}`}>
              {stats.failed_count}
            </span>
            <span className="text-[11.5px] text-neutral-700">need attention</span>
          </Link>
        </div>

        {/* Record quickly -- "Receipt" from the mockup has no dedicated
            route in this app (folded into other flows), so this uses
            Transfer as the fourth real destination instead. */}
        <div>
          <p className="text-[10.5px] tracking-[0.12em] uppercase text-neutral-700 mb-2.5">Record quickly</p>
          <div className="grid grid-cols-4 gap-2">
            {[
              { href: '/sales', label: 'Sale', Icon: ShoppingCart },
              { href: '/purchase', label: 'Purchase', Icon: ShoppingBag },
              { href: '/payment', label: 'Payment', Icon: CreditCard },
              { href: '/transfer', label: 'Transfer', Icon: ArrowRightLeft },
            ].map(({ href, label, Icon }) => (
              <Link
                key={href}
                href={href}
                className="h-[72px] flex flex-col items-center justify-center gap-1.5 border border-divider rounded-md text-[12.5px] hover:border-accent hover:text-accent-700 transition-colors"
              >
                <Icon className="w-4 h-4" />
                <span>{label}</span>
              </Link>
            ))}
          </div>
        </div>

        {/* Today -- Masters/Settings moved into the header's account sheet,
            matching the design (they're no longer duplicated here). */}
        <div>
          <div className="flex justify-between items-baseline mb-2.5">
            <h2 className="font-heading font-semibold text-xl">Today</h2>
            <Link href="/reporting/sales" className="text-[13px] text-accent-700 hover:underline">All reports</Link>
          </div>

          {activities.length === 0 ? (
            <EmptyState title="No recent activity" message="Nothing has been recorded yet today." />
          ) : (
            <div className="flex flex-col gap-3">
              {activities.map((activity) => (
                <ActivityRow key={activity.id} activity={activity} />
              ))}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
