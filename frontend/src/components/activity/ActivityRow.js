'use client';

import { getStatus, getIcon } from './activityHelpers';
import { StatusBadge } from '@/components/ui/StatusBadge';

const formatMoney = (v) => `₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

export const ActivityRow = ({ activity, onClick }) => {
  const status = getStatus(activity.status);
  const time = new Date(activity.created_at + (activity.created_at.includes('Z') ? '' : 'Z'))
    .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  const title = activity.party || activity.description || activity.operation_type;
  const subParts = [activity.type_label, time, activity.sub_label].filter(Boolean);

  // Only interactive where a caller actually wants a detail view (the Queue
  // page); the Home "Today" feed is a plain read-only glance, no click.
  const Tag = onClick ? 'button' : 'div';

  return (
    <Tag
      onClick={onClick}
      className={`w-full flex items-center gap-3 py-3.5 border-b border-divider text-left transition-colors ${onClick ? 'hover:bg-text/4' : ''}`}
    >
      <div className="w-10 h-10 rounded-full border border-divider flex items-center justify-center shrink-0">
        {getIcon(activity.type_label)}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[15px] truncate">{title}</p>
        <p className="text-sm text-neutral-700 truncate mt-0.5">{subParts.join(' · ')}</p>
        {status === 'failed' && activity.error_message && (
          <p className="text-[12.5px] text-accent-700 truncate mt-0.5">{activity.error_message}</p>
        )}
      </div>
      <div className="text-right shrink-0">
        {activity.amount != null && (
          <div className="font-heading font-semibold text-lg">
            {activity.amount < 0 ? '− ' : ''}{formatMoney(activity.amount)}
          </div>
        )}
        <StatusBadge status={status} label={status === 'synced' ? 'In Tally' : status === 'failed' ? 'Failed' : 'Waiting'} className="mt-1" />
      </div>
    </Tag>
  );
};
