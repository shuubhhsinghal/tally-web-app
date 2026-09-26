import React from 'react';
import { CheckCircle2, Clock, AlertCircle } from 'lucide-react';

export function StatusBadge({ status, label, className = '' }) {
  // status: 'synced' | 'waiting' | 'failed'
  // Matches the design system's convention of distinguishing sync states by
  // icon + label, not color -- "synced" is calm/neutral, "waiting" and
  // "failed" both use the single accent (no separate red/amber semantics).
  const variants = {
    synced: "border-divider text-neutral-700",
    waiting: "border-accent text-accent-700",
    failed: "border-accent text-accent-700",
  };

  const Icon = {
    synced: CheckCircle2,
    waiting: Clock,
    failed: AlertCircle
  }[status] || Clock;

  const defaultLabel = {
    synced: 'Sent to Tally',
    waiting: 'Waiting',
    failed: 'Failed'
  }[status] || 'Unknown';

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${variants[status] || variants.waiting} ${className}`}>
      <Icon className="w-3.5 h-3.5" />
      {label || defaultLabel}
    </span>
  );
}
