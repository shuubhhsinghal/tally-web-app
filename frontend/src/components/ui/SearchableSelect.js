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
  rawItemName = ''
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
        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 ml-1">
          {label}
        </label>
      )}

      <div
        onClick={() => setIsOpen(true)}
        className={`w-full min-h-[48px] px-4 rounded-xl border flex items-center justify-between cursor-text
          ${isOpen ? 'ring-2 ring-teal-500/50 border-teal-500' : (error ? 'border-red-500' : 'border-gray-200 dark:border-gray-700')}
          bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 transition-all`}
      >
        <div className="flex-1 flex items-center h-full">
          {isOpen ? (
            <input
              autoFocus
              type="text"
              className="w-full h-full bg-transparent outline-none placeholder-gray-400 dark:placeholder-gray-500 text-base"
              placeholder="Type to search..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          ) : (
            <span className={`block truncate ${value ? "text-gray-900 dark:text-gray-100" : "text-gray-400 dark:text-gray-500"}`}>
              {value || placeholder}
            </span>
          )}
        </div>
        <ChevronDown className={`w-5 h-5 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''} shrink-0 ml-2`} />
      </div>

      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-2 max-h-60 overflow-y-auto bg-white dark:bg-slate-800 border border-gray-200 dark:border-slate-700 rounded-xl shadow-xl z-50 py-1">
          {onCreateNew && (query.trim() || rawItemName) && !isLoading && (
            <div
              onClick={() => { onCreateNew((query.trim()) || rawItemName); setQuery(''); setIsOpen(false); }}
              className="px-4 py-3 text-sm cursor-pointer transition-colors border-b border-dashed border-teal-200 dark:border-teal-900/40 text-teal-700 dark:text-teal-400 font-bold bg-teal-50/60 dark:bg-teal-900/20 hover:bg-teal-100 dark:hover:bg-teal-900/40"
            >
              + Create &quot;{query.trim() || rawItemName}&quot; as new Tally item
            </div>
          )}
          {isLoading ? (
            <div className="px-4 py-3 text-sm text-gray-400 text-center flex items-center justify-center gap-2">
              <svg className="animate-spin w-4 h-4 text-teal-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
              </svg>
              Loading items...
            </div>
          ) : filteredOptions.length === 0 ? (
            <div className="px-4 py-3 text-sm text-gray-500 text-center">No results found</div>
          ) : (
            filteredOptions.map((opt, idx) => (
              <div
                key={idx}
                onClick={() => handleSelect(opt)}
                className={`px-4 py-3 text-sm cursor-pointer transition-colors border-b border-gray-50 dark:border-gray-800/50 last:border-0
                  ${value === opt ? 'bg-teal-50 dark:bg-teal-900/30 text-teal-700 dark:text-teal-400 font-bold' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'}
                `}
              >
                {opt}
              </div>
            ))
          )}
        </div>
      )}
      {error && <span className="text-xs text-red-500 ml-1 mt-0.5">{error}</span>}
    </div>
  );
}
