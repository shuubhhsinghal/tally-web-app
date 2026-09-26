import React from 'react';
import { PackageOpen } from 'lucide-react';

export function EmptyState({ title = "Nothing here yet", message, icon: Icon = PackageOpen }) {
  return (
    <div className="flex flex-col items-center justify-center p-8 text-center rounded-md border border-dashed border-divider my-4">
      <div className="w-16 h-16 rounded-full border border-divider flex items-center justify-center mb-4">
        <Icon className="w-8 h-8 text-neutral-500" />
      </div>
      <h3 className="font-heading font-semibold text-lg mb-1">{title}</h3>
      {message && <p className="text-sm text-neutral-700 max-w-[250px]">{message}</p>}
    </div>
  );
}
