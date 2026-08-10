import React from 'react';

export function Select({ label, error, children, className = '', ...props }) {
  return (
    <div className={`w-full flex flex-col gap-1.5 ${className}`}>
      {label && (
        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 ml-1">
          {label}
        </label>
      )}
      <div className="relative">
        <select 
          className={`w-full min-h-[48px] px-4 pr-10 appearance-none rounded-xl border bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-base
            focus:outline-none focus:ring-2 focus:ring-teal-500/50 focus:border-teal-500 transition-all
            ${error ? 'border-red-500 focus:ring-red-500/50 focus:border-red-500' : 'border-gray-200 dark:border-gray-700'}
          `}
          {...props}
        >
          {children}
        </select>
        <div className="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none text-gray-400">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>
      {error && <span className="text-xs text-red-500 ml-1 mt-0.5">{error}</span>}
    </div>
  );
}
