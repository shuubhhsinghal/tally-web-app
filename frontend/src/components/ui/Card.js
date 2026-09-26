import React from 'react';

export function Card({ children, className = '', onClick, ...props }) {
  const isClickable = !!onClick;
  
  return (
    <div 
      onClick={onClick}
      className={`bg-transparent rounded-md border border-divider p-4 ${isClickable ? 'cursor-pointer hover:bg-text/4 transition-colors active:scale-[0.99]' : ''} ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}
