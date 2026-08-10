import React from 'react';

export function Card({ children, className = '', onClick, ...props }) {
  const isClickable = !!onClick;
  
  return (
    <div 
      onClick={onClick}
      className={`bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 shadow-sm ${isClickable ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors active:scale-[0.99]' : ''} ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}
