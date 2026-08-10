import React from 'react';
import { CheckCircle2, Clock, XCircle } from 'lucide-react';

export function StatusBadge({ status, label, className = '' }) {
  // status: 'synced' | 'waiting' | 'failed'
  const variants = {
    synced: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 border-green-200 dark:border-green-800",
    waiting: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800",
    failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800"
  };

  const Icon = {
    synced: CheckCircle2,
    waiting: Clock,
    failed: XCircle
  }[status] || Clock;

  const defaultLabel = {
    synced: 'Sent to Tally',
    waiting: 'Waiting',
    failed: 'Failed'
  }[status] || 'Unknown';

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${variants[status] || variants.waiting} ${className}`}>
      <Icon className="w-3.5 h-3.5" />
      {label || defaultLabel}
    </span>
  );
}
