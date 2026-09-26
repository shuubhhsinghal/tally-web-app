import React from 'react';

export function Button({
  children,
  variant = 'primary',
  className = '',
  disabled = false,
  ...props
}) {
  const baseStyles = "w-full min-h-[48px] px-4 rounded-md font-heading font-semibold text-base transition-colors flex items-center justify-center gap-2 disabled:opacity-45 disabled:cursor-not-allowed";

  // Matches the design system's outlined/bordered button language (no
  // filled buttons) -- "danger" is the one deliberate exception, kept
  // semantically red since the source design never shows a destructive
  // confirmation to copy exactly, and real delete actions need the cue.
  const variants = {
    primary: "border border-accent text-accent hover:bg-accent/12 active:bg-accent/22",
    secondary: "border border-divider text-text hover:bg-text/7 active:bg-text/14",
    danger: "bg-red-600 hover:bg-red-700 text-white shadow-sm"
  };

  return (
    <button 
      className={`${baseStyles} ${variants[variant]} ${className}`}
      disabled={disabled}
      {...props}
    >
      {children}
    </button>
  );
}
