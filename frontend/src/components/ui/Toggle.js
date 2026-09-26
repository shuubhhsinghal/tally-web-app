import React from 'react';

export function Toggle({ checked, onChange, disabled = false, className = '', ...props }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange && onChange(!checked)}
      className={`relative inline-flex h-7 w-12 items-center rounded-full border transition-colors disabled:opacity-50 ${
        checked ? 'bg-accent border-accent' : 'bg-transparent border-divider'
      } ${className}`}
      {...props}
    >
      <span
        className={`inline-block h-5 w-5 transform rounded-full transition-transform ${
          checked ? 'translate-x-6 bg-bg' : 'translate-x-1 bg-neutral-500'
        }`}
      />
    </button>
  );
}
