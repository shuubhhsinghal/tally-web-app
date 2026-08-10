import React from 'react';

export function Input({ label, error, className = '', ...props }) {
  return (
    <div className={`w-full flex flex-col gap-1.5 ${className}`}>
      {label && (
        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 ml-1">
          {label}
        </label>
      )}
      <input 
        className={`w-full min-h-[48px] px-4 rounded-xl border bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-base
          placeholder-gray-400 dark:placeholder-gray-500
          focus:outline-none focus:ring-2 focus:ring-teal-500/50 focus:border-teal-500 transition-all
          ${error ? 'border-red-500 focus:ring-red-500/50 focus:border-red-500' : 'border-gray-200 dark:border-gray-700'}
        `}
        {...props}
      />
      {error && <span className="text-xs text-red-500 ml-1 mt-0.5">{error}</span>}
    </div>
  );
}
