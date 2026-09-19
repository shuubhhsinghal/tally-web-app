'use client';

import React, { useState, useEffect } from 'react';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Database } from 'lucide-react';
import Link from 'next/link';
import { useUI } from '@/context/UIContext';
import { ActivityRow } from '@/components/activity/ActivityRow';
import { TransactionDetailView } from '@/components/activity/TransactionDetailView';

export default function Dashboard() {
  const { showToast } = useUI();
  const [stats, setStats] = useState({ queue_count: 0, cache_count: 0, failed_count: 0, tally_online: false });
  const [activities, setActivities] = useState([]);
  const [mounted, setMounted] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState(null);

  const fetchStats = async () => {
    try {
      const res = await fetch('/api/dashboard/stats');
      if (res.ok) {
        const data = await res.json();
        setStats(data);
        localStorage.setItem('dash_stats', JSON.stringify(data));
      }

      const actRes = await fetch('/api/dashboard/activity');
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

        {/* Summary Card */}
        <Card className="flex justify-between items-center px-6 py-5 bg-white dark:bg-gray-800">
          <div className="flex flex-col items-center">
            <span className="text-3xl font-black text-amber-500">{stats.queue_count}</span>
            <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 mt-1 uppercase tracking-wider">Waiting</span>
          </div>
          <div className="w-px h-10 bg-gray-100 dark:bg-gray-700" />
          <div className="flex flex-col items-center">
            <span className="text-3xl font-black text-red-500">
              {stats.failed_count}
            </span>
            <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 mt-1 uppercase tracking-wider">Failed</span>
            {stats.failed_count > 0 && (
              <Link
                href="/queue?status=FAILED"
                className="text-[10px] font-bold text-teal-600 dark:text-teal-400 hover:underline mt-0.5"
              >
                View All &rarr;
              </Link>
            )}
          </div>
        </Card>

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
