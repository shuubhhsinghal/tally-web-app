'use client';

import { Card } from '@/components/ui/Card';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { getStatus, getIconType, getIcon } from './activityHelpers';

export const ActivityRow = ({ activity, onClick }) => (
  <Card
    onClick={onClick}
    className="flex items-center gap-4 p-4 cursor-pointer hover:ring-2 hover:ring-teal-500/50 transition-all active:scale-[0.98]"
  >
    <div className="w-10 h-10 rounded-full bg-gray-50 dark:bg-gray-700 flex items-center justify-center shrink-0">
      {getIcon(getIconType(activity.operation_type))}
    </div>
    <div className="flex-1 min-w-0">
      <p className="text-sm font-bold text-gray-900 dark:text-gray-100 truncate">
        {activity.description || activity.operation_type}
      </p>
      <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mt-0.5">
        {new Date(activity.created_at + (activity.created_at.includes('Z') ? '' : 'Z')).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </p>
    </div>
    <StatusBadge status={getStatus(activity.status)} />
  </Card>
);
