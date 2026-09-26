import React, { useState, useEffect, useRef } from "react";
import { Search } from "lucide-react";

export function MasterAutocomplete({ value, onChange, placeholder, confirmed = [], masterStates = [], onCreate, disabled, rawItemName = "", inputClassName = "", createLabel = "item", isCreating = false, itemSubtext = "In Tally", icon: Icon }) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [hasEditedSearch, setHasEditedSearch] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(-1);
  const wrapperRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (isOpen && inputRef.current) inputRef.current.focus();
  }, [isOpen]);

  const openDropdown = () => {
    if (disabled) return;
    if (!hasEditedSearch) setSearch(value || rawItemName || "");
    setIsOpen(true);
  };

  const normalize = (s) => {
    if (!s) return "";
    return s.toLowerCase()
      .replace(/\s+/g, ' ')
      .replace(/\s*\(\s*/g, '(')
      .replace(/\s*\)\s*/g, ')')
      .trim();
  };

  const merged = [];
  const seen = new Set();

  const add = (name, state, original_name, error) => {
    const norm = normalize(name);
    if (!seen.has(norm)) {
      seen.add(norm);
      merged.push({ name: original_name || name, norm, state, error });
    }
  };

  confirmed.forEach(name => add(name, "confirmed", name, null));
  if (masterStates) {
    masterStates.filter(m => m.state === "syncing").forEach(m => add(m.normalized_name, "syncing", m.name, null));
    masterStates.filter(m => m.state === "pending").forEach(m => add(m.normalized_name, "pending", m.name, null));
    masterStates.filter(m => m.state === "failed").forEach(m => add(m.normalized_name, "failed", m.name, m.error));
  }

  const calculateSimilarity = (s1, s2) => {
    if (!s1 || !s2) return 0;
    if (s1 === s2) return 1;
    const getBigrams = (str) => {
      const bigrams = new Set();
      for (let i = 0; i < str.length - 1; i++) bigrams.add(str.substring(i, i + 2));
      return bigrams;
    };
    const b1 = getBigrams(s1);
    const b2 = getBigrams(s2);
    let intersection = 0;
    for (const b of b1) {
      if (b2.has(b)) intersection++;
    }
    if (b1.size + b2.size === 0) return 0;
    return (2.0 * intersection) / (b1.size + b2.size);
  };

  const normSearch = normalize(search);
  const filtered = merged.map(m => {
    const sim = calculateSimilarity(m.norm, normSearch);
    const isSubstring = m.norm.includes(normSearch) || normSearch.includes(m.norm);
    return { ...m, sim, isSubstring };
  }).filter(m => m.isSubstring || m.sim >= 0.5)
    .sort((a, b) => b.sim - a.sim);

  const exactMatch = filtered.find(m => m.norm === normSearch);

  useEffect(() => {
    function handleClickOutside(event) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target)) setIsOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFocusedIndex(-1);
  }, [search, isOpen]);

  const handleKeyDown = (e) => {
    if (!isOpen) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') setIsOpen(true);
      return;
    }
    const showCreate = (!exactMatch) && search.trim() !== "" && onCreate;
    const maxIndex = filtered.length - 1 + (showCreate ? 1 : 0);

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setFocusedIndex(prev => (prev < maxIndex ? prev + 1 : prev));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setFocusedIndex(prev => (prev > 0 ? prev - 1 : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (focusedIndex >= 0 && focusedIndex < filtered.length) {
        const item = filtered[focusedIndex];
        if (item.state === 'failed') {
          alert(`Could not sync this ${createLabel} to Tally. Resolve it from Dashboard before using it.`);
        } else {
          setHasEditedSearch(false);
          onChange(item.name);
          setIsOpen(false);
        }
      } else if (focusedIndex === filtered.length && showCreate) {
        setHasEditedSearch(false);
        onCreate(search.trim());
        setIsOpen(false);
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false);
    }
  };

  // Matches the app's monochrome-plus-gold convention: "confirmed" is calm/
  // neutral, everything else (still in progress or needs attention) uses
  // the single accent -- states are told apart by icon + label, not color.
  const badgeConfig = {
    confirmed: { text: "✓ In Tally", cls: "text-neutral-700 bg-transparent" },
    syncing: { text: "↻ Confirming", cls: "text-accent-700 bg-accent/8" },
    pending: { text: "⏳ Pending", cls: "text-accent-700 bg-accent/8" },
    failed: { text: "⚠ Failed", cls: "text-accent-700 bg-accent/8" }
  };

  return (
    <div className="relative w-full" ref={wrapperRef}>
      {/* CLOSED STATE: The trigger button */}
      <div
        role="combobox"
        aria-controls="listbox"
        aria-expanded={isOpen}
        aria-disabled={disabled}
        tabIndex={disabled ? -1 : 0}
        onClick={openDropdown}
        onFocus={openDropdown}
        onKeyDown={(e) => {
          if (disabled) return;
          if (e.key === 'Enter' || e.key === 'ArrowDown') {
            e.preventDefault();
            openDropdown();
          }
        }}
        className={`w-full min-h-[48px] px-4 py-2.5 flex items-center rounded border outline-none transition-colors whitespace-pre-wrap break-words ${disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-text'} ${inputClassName || "border-divider bg-bg text-text focus:border-accent"} ${Icon ? 'pl-10' : ''}`}
      >
        {Icon && (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-500">
            <Icon className="w-5 h-5" />
          </div>
        )}
        {value ? (
          <span>{value}</span>
        ) : (
          <span className="text-neutral-500">{placeholder}</span>
        )}
      </div>

      {/* OPEN STATE: Full Screen Search Modal */}
      {isOpen && (
        <div className="fixed inset-0 z-[100] bg-bg flex flex-col sm:p-4">
          <div className="flex flex-col h-full bg-bg sm:max-w-xl sm:mx-auto sm:w-full sm:shadow-2xl sm:rounded-2xl sm:overflow-hidden">
            
            {/* Header: Search Bar & Cancel */}
            <div className="flex items-center gap-4 px-4 py-4 sm:px-6 sm:py-5 border-b border-divider bg-bg">
              <div className="flex-1 flex items-center bg-bg border border-accent rounded px-3 py-2.5 shadow-sm">
                <Search className="w-5 h-5 text-neutral-500 mr-2" />
                <input
                  ref={inputRef}
                  type="text"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setHasEditedSearch(true);
                  }}
                  onKeyDown={handleKeyDown}
                  className="w-full bg-transparent outline-none text-[16px] text-text placeholder:text-neutral-500 font-body"
                  placeholder={`Search ${createLabel}s`}
                />
              </div>
              <button 
                onClick={() => setIsOpen(false)} 
                className="text-accent-700 font-heading font-semibold text-[17px] focus:outline-none"
              >
                Cancel
              </button>
            </div>

            {/* List Area */}
            <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6" role="listbox">
              <h4 className="text-[11px] font-bold text-neutral-600 uppercase tracking-widest mb-4">{createLabel.toUpperCase()}S</h4>
              
              <div className="flex flex-col">
                {filtered.map((item, idx) => {
                  const isFailed = item.state === 'failed';
                  const isFocused = idx === focusedIndex;
                  
                  return (
                    <div
                      key={idx}
                      role="option"
                      aria-selected={isFocused}
                      className={`py-3.5 border-b border-divider last:border-0 flex flex-col justify-center ${isFailed ? 'opacity-75 cursor-not-allowed' : 'cursor-pointer'} ${isFocused ? 'bg-text/5 -mx-4 px-4' : ''}`}
                      onClick={() => {
                        if (isFailed) {
                          alert(`Could not sync this ${createLabel} to Tally. Resolve it from Dashboard before using it.`);
                        } else {
                          setHasEditedSearch(false);
                          onChange(item.name);
                          setIsOpen(false);
                        }
                      }}
                    >
                      <div className="flex justify-between items-start">
                        <div className="flex flex-col">
                          <span className="text-[18px] font-heading font-semibold text-text leading-tight">
                            {item.name}
                          </span>
                          {isFailed ? (
                            <span className="text-[13px] font-body text-accent-700 mt-1 font-medium">{item.error}</span>
                          ) : (
                            <span className="text-[13px] font-body text-neutral-600 mt-1">
                              {createLabel === "supplier" ? "Supplier" : (badgeConfig[item.state] ? badgeConfig[item.state].text : itemSubtext)}
                            </span>
                          )}
                        </div>
                        {createLabel === "supplier" && (
                          <span className="text-[14px] font-heading font-semibold text-neutral-700 mt-0.5 whitespace-nowrap">
                            {/* We can show balance here if available in the future. For now, empty or mock if needed. The user image shows 38,200 Cr. We will leave it empty as data doesn't exist. */}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Create New / Open Masters */}
              <div className="mt-10 text-center pb-12">
                <span className="text-[15px] font-body text-neutral-600">Not listed? </span>
                {(!exactMatch) && search.trim() !== "" && onCreate ? (
                  <button 
                    onClick={() => {
                      if (isCreating) return;
                      setHasEditedSearch(false);
                      onCreate(search.trim());
                    }}
                    className="text-accent-700 font-heading font-semibold text-[17px] underline underline-offset-4 focus:outline-none"
                  >
                    {isCreating ? "Creating..." : `Create as ${createLabel}`}
                  </button>
                ) : (
                  <button 
                    onClick={() => alert("Open Masters feature coming soon.")}
                    className="text-accent-700 font-heading font-semibold text-[17px] underline underline-offset-4 focus:outline-none"
                  >
                    Open masters
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
