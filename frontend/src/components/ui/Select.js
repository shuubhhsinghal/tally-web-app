import React from 'react';

export function Select({ label, error, icon: Icon, children, className = '', ...props }) {
  return (
    <div className={`w-full flex flex-col gap-1.5 ${className}`}>
      {label && (
        <label className="text-xs text-text/70 ml-1">
          {label}
        </label>
      )}
      <div className="relative">
        {Icon && (
          <div className="absolute inset-y-0 left-0 flex items-center pl-4 pointer-events-none text-neutral-600">
            <Icon className="w-4.5 h-4.5" />
          </div>
        )}
        <select
          className={`w-full min-h-[48px] ${Icon ? 'pl-11' : 'px-4'} pr-10 appearance-none rounded-md border bg-transparent text-text text-base
            focus:outline-none focus-visible:border-accent transition-colors hover:border-text/45
            ${error ? 'border-accent' : 'border-divider'}
          `}
          {...props}
        >
          {children}
        </select>
        <div className="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none text-neutral-500">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>
      {error && <span className="text-xs text-accent-800 ml-1 mt-0.5">{error}</span>}
    </div>
  );
}
