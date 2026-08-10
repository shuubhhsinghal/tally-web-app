import React from 'react';

export function Button({ 
  children, 
  variant = 'primary', 
  className = '', 
  disabled = false, 
  ...props 
}) {
  const baseStyles = "w-full min-h-[48px] px-4 rounded-xl font-medium text-base transition-colors flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed";
  
  const variants = {
    primary: "bg-teal-600 hover:bg-teal-700 text-white shadow-sm",
    secondary: "bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-700",
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
