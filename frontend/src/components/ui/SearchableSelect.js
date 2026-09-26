'use client';

import React, { useState, useEffect, useRef } from 'react';
import { ChevronDown } from 'lucide-react';

export function SearchableSelect({
  options = [],
  value,
  onChange,
  placeholder = "Search...",
  label,
  error,
  isLoading = false,
  className = '',
  onCreateNew = null,
  rawItemName = '',
  createLabel = 'new Tally item',
  icon: Icon = null,
  sub = null,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const wrapperRef = useRef(null);

  // Close when clicking outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target)) {
        setIsOpen(false);
        // Reset query when closing without selection
        setQuery('');
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const filteredOptions = options
    .filter(opt => (opt || "").toLowerCase().includes(query.toLowerCase()))
    .slice(0, 50); // limit to top 50 matches

  const handleSelect = (option) => {
    onChange(option);
    setQuery('');
    setIsOpen(false);
  };

  return (
    <div className={`relative w-full flex flex-col gap-1.5 ${className}`} ref={wrapperRef}>
      {label && (
        <label className="text-xs text-text/70 ml-1">
          {label}
        </label>
      )}

      <div
        onClick={() => setIsOpen(true)}
        className={`w-full min-h-[48px] px-4 rounded-md border flex items-center gap-2.5 justify-between cursor-text
          ${isOpen ? 'border-accent' : (error ? 'border-accent' : 'border-divider hover:border-text/45')}
          bg-transparent text-text transition-colors`}
      >
        {Icon && <Icon className="w-4.5 h-4.5 text-neutral-600 shrink-0" />}
        <div className="flex-1 flex items-center h-full min-w-0">
          {isOpen ? (
            <input
              autoFocus
              type="text"
              className="w-full h-full bg-transparent outline-none placeholder-neutral-500 text-base"
              placeholder="Type to search..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          ) : (
            <span className="flex flex-col min-w-0 py-1.5">
              <span className={`block truncate ${value ? "text-text" : "text-neutral-500"}`}>
                {value || placeholder}
              </span>
              {sub && <span className="block truncate text-xs text-neutral-600 [font-feature-settings:'tnum']">{sub}</span>}
            </span>
          )}
        </div>
        <ChevronDown className={`w-5 h-5 text-neutral-500 transition-transform ${isOpen ? 'rotate-180' : ''} shrink-0 ml-2`} />
      </div>

      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-2 max-h-60 overflow-y-auto bg-surface border border-divider rounded-md shadow-lg z-50 py-1">
          {onCreateNew && (query.trim() || rawItemName) && !isLoading && (
            <div
              onClick={() => { onCreateNew((query.trim()) || rawItemName); setQuery(''); setIsOpen(false); }}
              className="px-4 py-3 text-sm cursor-pointer transition-colors border-b border-dashed border-accent text-accent-700 font-semibold bg-accent/7 hover:bg-accent/12"
            >
              + Create &quot;{query.trim() || rawItemName}&quot; as {createLabel}
            </div>
          )}
          {isLoading ? (
            <div className="px-4 py-3 text-sm text-neutral-500 text-center flex items-center justify-center gap-2">
              <svg className="animate-spin w-4 h-4 text-accent" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
              </svg>
              Loading items...
            </div>
          ) : filteredOptions.length === 0 ? (
            <div className="px-4 py-3 text-sm text-neutral-500 text-center">No results found</div>
          ) : (
            filteredOptions.map((opt, idx) => (
              <div
                key={idx}
                onClick={() => handleSelect(opt)}
                className={`px-4 py-3 text-sm cursor-pointer transition-colors border-b border-divider last:border-0
                  ${value === opt ? 'bg-accent/10 text-accent-700 font-semibold' : 'text-text hover:bg-text/5'}
                `}
              >
                {opt}
              </div>
            ))
          )}
        </div>
      )}
      {error && <span className="text-xs text-accent-800 ml-1 mt-0.5">{error}</span>}
    </div>
  );
}
