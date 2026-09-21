'use client';

import React, { useState, useEffect } from 'react';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Database, TrendingUp, Clock, AlertCircle } from 'lucide-react';
import Link from 'next/link';
import { useUI } from '@/context/UIContext';
import { ActivityRow } from '@/components/activity/ActivityRow';
import { TransactionDetailView } from '@/components/activity/TransactionDetailView';

const formatRupees = (amount) => `₹${Math.round(amount || 0).toLocaleString('en-IN')}`;

export default function Dashboard() {
  const { showToast } = useUI();
  const [stats, setStats] = useState({ queue_count: 0, cache_count: 0, failed_count: 0, today_sales: 0, today_sales_pending_count: 0, tally_online: false });
  const [activities, setActivities] = useState([]);
  const [mounted, setMounted] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState(null);

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
    const interval = setInterval(() => {
      if (!selectedItemId) fetchStats();
    }, 5000);
    return () => clearInterval(interval);
  }, [selectedItemId]);

  const handleClearFinished = async () => {
    try {
      const res = await fetch('/api/dashboard/activity/clear', { method: 'POST' });
      if (!res.ok) throw new Error("Clear failed");
      showToast("Cleared finished activities");
      fetchStats();
    } catch (err) {
      console.error(err);
      showToast("Failed to clear", "error");
    }
  };

  if (!mounted) return null;

  if (selectedItemId) {
    return (
      <TransactionDetailView
        itemId={selectedItemId}
        onBack={() => setSelectedItemId(null)}
        onMutated={fetchStats}
      />
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar title="Mom's Pride" />

      <div className="max-w-md mx-auto p-4 space-y-6 mt-4">

        {/* Hero: Today's Sales -- the number a shop owner actually cares
            about, leading the page instead of internal sync counts. */}
        <Card className="bg-gradient-to-br from-teal-600 to-teal-700 dark:from-teal-700 dark:to-teal-900 border-0 px-6 py-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-teal-100">Today&apos;s Sales</p>
              <p className="text-4xl font-black text-white mt-1">{formatRupees(stats.today_sales)}</p>
              {stats.today_sales_pending_count > 0 && (
                <p className="text-xs font-medium text-teal-100 mt-1.5">
                  {stats.today_sales_pending_count} sale{stats.today_sales_pending_count === 1 ? '' : 's'} still syncing to Tally
                </p>
              )}
            </div>
            <div className="w-12 h-12 rounded-full bg-white/15 flex items-center justify-center shrink-0">
              <TrendingUp className="w-6 h-6 text-white" />
            </div>
          </div>
        </Card>

        {/* Queue health -- quiet by default, only draws the eye when
            something actually needs attention. */}
        <div className="flex gap-3">
          <Link href="/queue?status=PENDING" className="flex-1">
            <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:ring-2 hover:ring-teal-500/50 transition-all">
              <Clock className="w-4 h-4 text-gray-400 shrink-0" />
              <div className="min-w-0">
                <p className="text-base font-bold text-gray-900 dark:text-gray-100 leading-none">{stats.queue_count}</p>
                <p className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mt-1">Waiting</p>
              </div>
            </div>
          </Link>
          <Link href="/queue?status=FAILED" className="flex-1">
            <div className={`flex items-center gap-2.5 px-4 py-3 rounded-xl border transition-all hover:ring-2 ${stats.failed_count > 0
              ? 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 hover:ring-red-500/50'
              : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:ring-teal-500/50'
              }`}>
              <AlertCircle className={`w-4 h-4 shrink-0 ${stats.failed_count > 0 ? 'text-red-500' : 'text-gray-400'}`} />
              <div className="min-w-0">
                <p className={`text-base font-bold leading-none ${stats.failed_count > 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-900 dark:text-gray-100'}`}>
                  {stats.failed_count}
                </p>
                <p className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mt-1">Failed</p>
              </div>
            </div>
          </Link>
        </div>

        {/* Quick Links */}
        <div className="flex gap-3">
          <Link href="/masters" className="flex-1">
            <Card className="flex items-center justify-center gap-2 py-3 hover:ring-2 hover:ring-teal-500/50 transition-all cursor-pointer bg-white dark:bg-gray-800">
              <Database className="w-4 h-4 text-teal-600" />
              <span className="text-sm font-bold text-gray-900 dark:text-gray-100">Masters Overview</span>
            </Card>
          </Link>
        </div>

        {/* Recent Activity */}
        <div className="space-y-3">
          <div className="flex justify-between items-center px-1">
            <h2 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Recent Activity</h2>
            <button
              onClick={handleClearFinished}
              className="text-[10px] font-bold uppercase tracking-wider text-teal-600 dark:text-teal-400 hover:text-teal-700 hover:underline"
            >
              Clear Finished
            </button>
          </div>

          {activities.length === 0 ? (
            <EmptyState title="No recent activity" message="Nothing has been recorded yet today." />
          ) : (
            <div className="flex flex-col gap-3">
              {activities.map((activity) => (
                <ActivityRow
                  key={activity.id}
                  activity={activity}
                  onClick={() => setSelectedItemId(activity.id)}
                />
              ))}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
