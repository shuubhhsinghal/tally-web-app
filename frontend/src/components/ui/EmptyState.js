import React from 'react';
import { PackageOpen } from 'lucide-react';

export function EmptyState({ title = "Nothing here yet", message, icon: Icon = PackageOpen }) {
  return (
    <div className="flex flex-col items-center justify-center p-8 text-center bg-gray-50 dark:bg-gray-800/50 rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 my-4">
      <div className="w-16 h-16 bg-white dark:bg-gray-800 rounded-full flex items-center justify-center shadow-sm mb-4">
        <Icon className="w-8 h-8 text-gray-400 dark:text-gray-500" />
      </div>
      <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">{title}</h3>
      {message && <p className="text-sm text-gray-500 dark:text-gray-400 max-w-[250px]">{message}</p>}
    </div>
  );
}
