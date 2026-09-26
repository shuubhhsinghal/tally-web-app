import React from 'react';

export function Input({ label, error, className = '', ...props }) {
  return (
    <div className={`w-full flex flex-col gap-1.5 ${className}`}>
      {label && (
        <label className="text-xs text-text/70 ml-1">
          {label}
        </label>
      )}
      <input
        className={`appearance-none min-w-0 w-full min-h-[48px] px-4 rounded-md border bg-transparent text-text text-base
          placeholder-neutral-500
          focus:outline-none focus-visible:border-accent transition-colors hover:border-text/45
          ${error ? 'border-accent' : 'border-divider'}
        `}
        {...props}
      />
      {error && <span className="text-xs text-accent-800 ml-1 mt-0.5">{error}</span>}
    </div>
  );
}
