'use client';
import { useState, useEffect, useRef, useMemo } from "react";
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { useSearchParams, useRouter } from 'next/navigation';
import { UploadCloud, Image as ImageIcon, AlertCircle, ChevronDown, ChevronRight, PlusCircle, Trash2, Edit3, Camera, History, X } from "lucide-react";
import { calculateAndReconcileV4 } from './utils/reconciliation';
import { CameraCapture } from './CameraCapture';

function MasterAutocomplete({ value, onChange, placeholder, confirmed = [], masterStates = [], onCreate, disabled, rawItemName = "", inputClassName = "", createLabel = "item", isCreating = false }) {
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

  // Deduplicate and rank: confirmed > syncing > pending > failed
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
          alert("Could not sync this supplier to Tally. Resolve it from Dashboard before using it.");
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

  const badgeConfig = {
    confirmed: { text: "✓ In Tally", cls: "text-green-600 bg-green-50 dark:bg-green-900/30" },
    syncing: { text: "↻ Confirming", cls: "text-blue-600 bg-blue-50 dark:bg-blue-900/30" },
    pending: { text: "⏳ Pending", cls: "text-amber-600 bg-amber-50 dark:bg-amber-900/30" },
    failed: { text: "⚠ Failed", cls: "text-red-600 bg-red-50 dark:bg-red-900/30" }
  };

  return (
    <div className="relative w-full" ref={wrapperRef}>
      {isOpen ? (
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded={isOpen}
          aria-controls="listbox"
          type="text"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setHasEditedSearch(true);
          }}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          className={`w-full min-h-[48px] px-4 rounded-xl border outline-none transition-colors ${inputClassName || "border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-teal-500/50"}`}
          placeholder={placeholder}
        />
      ) : (
        // Closed state is a wrapping div, not an <input> -- inputs are
        // permanently single-line (white-space CSS has no effect on them),
        // so a long selected item name would always show clipped otherwise.
        <div
          role="combobox"
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
          className={`w-full min-h-[48px] px-4 py-2.5 flex items-center rounded-xl border outline-none transition-colors whitespace-pre-wrap break-words ${disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-text'} ${inputClassName || "border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-teal-500/50"}`}
        >
          {value ? (
            <span>{value}</span>
          ) : (
            <span className="text-gray-400">{placeholder}</span>
          )}
        </div>
      )}

      {isOpen && (
        <div className="absolute z-50 w-full mt-2 bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-100 dark:border-gray-700 overflow-hidden">
          {/* Only the match list scrolls -- "Create new" below is a pinned
              footer outside this box, so it stays reachable with no scrolling
              even when many similar items (e.g. several pack sizes of the
              same product) fill the list. */}
          <div className="max-h-60 overflow-y-auto" role="listbox">
            {filtered.map((item, idx) => {
              const isFailed = item.state === 'failed';
              const isFocused = idx === focusedIndex;
              return (
                <div
                  key={idx}
                  role="option"
                  aria-selected={isFocused}
                  className={`px-4 py-3 border-b border-gray-50 dark:border-gray-700 last:border-0 flex items-center justify-between ${isFailed ? 'opacity-75 cursor-not-allowed' : 'cursor-pointer'} ${isFocused ? 'bg-gray-100 dark:bg-gray-700' : 'hover:bg-gray-50 dark:hover:bg-gray-700'}`}
                  onClick={() => {
                    if (isFailed) {
                      alert("Could not sync this supplier to Tally. Resolve it from Dashboard before using it.");
                    } else {
                      setHasEditedSearch(false);
                      onChange(item.name);
                      setIsOpen(false);
                    }
                  }}
                >
                  <div className="flex flex-col">
                    <span className="text-sm font-medium text-gray-900 dark:text-white">{item.name}</span>
                    {isFailed && <span className="text-xs text-red-500 mt-1">{item.error}</span>}
                  </div>
                  {badgeConfig[item.state] && (
                    <span className={`text-xs px-2 py-1 rounded-full whitespace-nowrap ${badgeConfig[item.state].cls}`}>
                      {badgeConfig[item.state].text}
                    </span>
                  )}
                </div>
              );
            })}
          </div>

          {(!exactMatch) && search.trim() !== "" && onCreate && (
            <div
              role="option"
              aria-selected={focusedIndex === filtered.length}
              className={`px-4 py-3 cursor-pointer text-teal-600 flex items-center gap-2 border-t border-gray-100 dark:border-gray-700 ${isCreating ? 'opacity-50 cursor-not-allowed' : focusedIndex === filtered.length ? 'bg-teal-100 dark:bg-teal-900/50' : 'hover:bg-teal-50 dark:hover:bg-teal-900/30'}`}
              onClick={() => {
                if (isCreating) return;
                setHasEditedSearch(false);
                onCreate(search.trim());
              }}
            >
              {isCreating ? (
                <span className="text-sm font-medium">⏳ Creating {createLabel}...</span>
              ) : (
                <>
                  <PlusCircle className="w-4 h-4" />
                  <span className="text-sm font-medium">Create &quot;{search.trim()}&quot; as new Tally {createLabel}</span>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ItemWiseMode({ onPostSuccess }) {
  const { showToast, showConfirmDialog } = useUI();
  const { user } = useAuth();
  const lockedStore = user && !user.is_owner ? user.store_name : null;

  const [meta, setMeta] = useState({ suppliers: [], stock_items: [], uoms: [], stores: ["Mahagun", "Vvip", "Gulshan"] });
  const [itemCache, setItemCache] = useState({});

  const [file, setFile] = useState(null);
  const [isExtracting, setIsExtracting] = useState(false);
  const [invoice, setInvoice] = useState(null);
  const [items, setItems] = useState([]);
  const [editingItemIdx, setEditingItemIdx] = useState(null);
  const [showTaxEdit, setShowTaxEdit] = useState(false);
  const [adjustment, setAdjustment] = useState({
    enabled: false,
    store: "",
    reason: "",
    notes: "",
    items: [] // [{ name, uom, qty, rate, amount, rateStatus: 'idle'|'loading'|'ok'|'error', rateError }]
  });

  const [isPosting, setIsPosting] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);
  const [creatingMaster, setCreatingMaster] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [stagedFiles, setStagedFiles] = useState([]);
  const fileInputRef = useRef(null);
  const [showUploadOptions, setShowUploadOptions] = useState(false);
  const [showCamera, setShowCamera] = useState(false);
  // Gallery-picked files still needing a corner-marking pass, drained one at a time.
  const [filesToCrop, setFilesToCrop] = useState([]);
  const croppedBatchRef = useRef([]);

  const [validationStatus, setValidationStatus] = useState("matched"); // "matched", "unverified"
  const [totals, setTotals] = useState({ printed: 0, calculated: 0, diff: 0 });
  const [nativePhotoFile, setNativePhotoFile] = useState(null);
  const nativeCameraInputRef = useRef(null);
  const isAndroid = typeof navigator !== 'undefined' && /Android/i.test(navigator.userAgent);
  const [forceManual, setForceManual] = useState(false);

  // V4 Additions
  const [v4RawData, setV4RawData] = useState(null);
  const [v4Config, setV4Config] = useState({
    selected_amount_header: null,
    gst_basis_override: null,
    gst_recording_method: "included_in_rate"
  });

  const searchParams = useSearchParams();
  const router = useRouter();
  const draftIdParam = searchParams?.get('draftId');
  const [activeDraftId, setActiveDraftId] = useState(null);
  const [isDraftLoading, setIsDraftLoading] = useState(false);

  const loadDraft = async (id) => {
    try {
      setIsDraftLoading(true);
      const res = await fetch(`/api/purchase-drafts/${id}`);
      if (!res.ok) throw new Error("Failed to load draft");
      const data = await res.json();
      
      const parsedDraft = data.draft_data;
      if (parsedDraft.invoice) setInvoice(parsedDraft.invoice);
      if (parsedDraft.items) setItems(parsedDraft.items);
      if (parsedDraft.v4RawData) setV4RawData(parsedDraft.v4RawData);
      if (parsedDraft.v4Config) setV4Config(parsedDraft.v4Config);
      if (parsedDraft.adjustment) {
        // Defensive normalizer: an older draft may carry the legacy flat
        // {amount, store, reason, notes} shape (pre item-wise-return) --
        // drop the stale `amount` and default `items` so it doesn't crash
        // the new derived-total logic.
        setAdjustment({
          enabled: false, store: "", reason: "", notes: "", items: [],
          ...parsedDraft.adjustment,
          items: parsedDraft.adjustment.items || []
        });
      }
      if (parsedDraft.totals) setTotals(parsedDraft.totals);
      if (parsedDraft.validationStatus) setValidationStatus(parsedDraft.validationStatus);
      
      setPreviewUrl(data.image_path ? `${data.image_path}?v=${new Date(data.updated_at || Date.now()).getTime()}` : null); // Loaded from backend
      setActiveDraftId(id);
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setIsDraftLoading(false);
    }
  };

  useEffect(() => {
    if (draftIdParam) {
      // eslint-disable-next-line
      loadDraft(draftIdParam);
    }
  }, [draftIdParam]);

  // A staff account is locked to its own store regardless of how/when the
  // invoice object gets (re)created (extraction, manual entry, draft load).
  useEffect(() => {
    if (lockedStore && invoice && invoice.cost_center !== lockedStore) {
      setInvoice(prev => ({ ...prev, cost_center: lockedStore }));
    }
  }, [lockedStore, invoice]);

  useEffect(() => {
    if (lockedStore && adjustment.store !== lockedStore) {
      setAdjustment(prev => ({ ...prev, store: lockedStore }));
    }
  }, [lockedStore, adjustment.store]);



  // Auto-Save background worker
  useEffect(() => {
    if (!activeDraftId || isDraftLoading) return;
    
    const timer = setTimeout(() => {
      const draftData = JSON.stringify({
        invoice,
        items,
        v4RawData,
        v4Config,
        adjustment,
        totals,
        validationStatus
      });
      
      const formData = new FormData();
      formData.append("draft_data", draftData);
      
      fetch(`/api/purchase-drafts/${activeDraftId}`, {
        method: "PUT",
        body: formData
      }).catch(err => console.error("Auto-save failed", err));
    }, 1500); // 1.5s debounce
    
    return () => clearTimeout(timer);
  }, [invoice, items, v4Config, adjustment, totals, validationStatus, activeDraftId, isDraftLoading]);

  const v4Data = useMemo(() => {
    if (!v4RawData) return null;
    return calculateAndReconcileV4(
      v4RawData.extracted_data,
      v4Config.gst_recording_method,
      v4Config.gst_basis_override,
      invoice?.gst_rate || 0,
      v4Config.selected_amount_header
    );
  }, [v4RawData, v4Config, invoice?.gst_rate]);

  const returnTotal = useMemo(() => {
    return adjustment.items.reduce((sum, i) => sum + (Number(i.qty) || 0) * (Number(i.rate) || 0), 0);
  }, [adjustment.items]);

  const refreshReturnItemRate = async (name, qty) => {
    try {
      const res = await fetch('/api/purchase-item/return-item-rate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: name, qty })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'No rate found for this item.');
      setAdjustment(prev => ({
        ...prev,
        items: prev.items.map(i => i.name === name
          ? { ...i, rate: data.rate, amount: data.amount, rateStatus: 'ok', rateError: null }
          : i)
      }));
    } catch (err) {
      setAdjustment(prev => ({
        ...prev,
        items: prev.items.map(i => i.name === name
          ? { ...i, rate: 0, amount: 0, rateStatus: 'error', rateError: err.message }
          : i)
      }));
    }
  };

  const handleAddReturnItem = (name) => {
    if (!name || adjustment.items.some(i => i.name === name)) return;
    const uom = meta.stock_item_units?.[name] || 'PCS';
    const newRow = { name, uom, qty: 1, rate: 0, amount: 0, rateStatus: 'loading', rateError: null };
    setAdjustment(prev => ({ ...prev, items: [...prev.items, newRow] }));
    refreshReturnItemRate(name, 1);
  };

  const handleReturnItemQtyChange = (idx, qty) => {
    setAdjustment(prev => ({
      ...prev,
      items: prev.items.map((i, iidx) => iidx === idx ? { ...i, qty } : i)
    }));
  };

  const handleRemoveReturnItem = (idx) => {
    setAdjustment(prev => ({ ...prev, items: prev.items.filter((_, iidx) => iidx !== idx) }));
  };

  const [historyPicker, setHistoryPicker] = useState({ open: false, itemIdx: null, itemName: null, loading: false, error: null, source: null, entries: [] });

  const openHistoryPicker = async (idx, itemName) => {
    setHistoryPicker({ open: true, itemIdx: idx, itemName, loading: true, error: null, source: null, entries: [] });
    try {
      const url = '/api/purchase-item/return-item-history?' + new URLSearchParams({ item_name: itemName });
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to fetch purchase history.');
      setHistoryPicker(prev => ({ ...prev, loading: false, source: data.source, entries: data.entries || [] }));
    } catch (err) {
      setHistoryPicker(prev => ({ ...prev, loading: false, error: err.message }));
    }
  };

  const closeHistoryPicker = () => setHistoryPicker({ open: false, itemIdx: null, itemName: null, loading: false, error: null, source: null, entries: [] });

  const handleSelectHistoryRate = (entry) => {
    setAdjustment(prev => ({
      ...prev,
      items: prev.items.map((i, iidx) => iidx === historyPicker.itemIdx
        ? { ...i, rate: entry.rate, rateStatus: 'ok', rateError: null }
        : i)
    }));
    closeHistoryPicker();
  };

  useEffect(() => {
    if (v4Data && v4Data.calculated_data) {
      const mappedLegacyItems = v4Data.calculated_data.items.map(item => ({
        name: item.name,
        qty: item.qty,
        rate: item.rate,
        amount: item.ex_gst_amount,
        final_amount: item.final_amount,
        uom: item.uom,
        mapped_name: item.mapped_name,
        mapped_unit: item.mapped_unit,
        is_mapped: item.is_mapped
      }));
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setItems(mappedLegacyItems);
    }
  }, [v4Data]);

  useEffect(() => {
    fetch("/api/purchase-item/metadata")
      .then(res => res.json())
      .then(data => setMeta(prev => ({ ...prev, ...data })))
      .catch(err => console.error(err));

    fetch('/api/purchase-item/items')
      .then(res => res.json())
      .then(data => setItemCache(data))
      .catch(err => console.error(err));
  }, []);

  const handleTaxRecalculation = (newRate, newTaxType, newGstMethod = null) => {
    const rate = Number(newRate);
    const methodToUse = newGstMethod || (v4Config?.gst_recording_method || "included_in_rate");
    const gstBasis = v4Config?.gst_basis_override || "exclusive";

    let updatedItems = [...items];

    updatedItems = updatedItems.map(item => {
      let exAmt = Number(item.amount !== undefined ? item.amount : (item.final_amount || 0));
      let exRate = Number(item.rate !== undefined ? item.rate : (item.final_rate || 0));
      const fAmt = Number(item.final_amount !== undefined ? item.final_amount : (item.amount || 0));
      const fRate = Number(item.final_rate !== undefined ? item.final_rate : (item.rate || 0));
      
      let newExAmt = exAmt;
      let newExRate = exRate;
      let newFAmt = fAmt;
      let newFRate = fRate;

      // V4 mode prediction (will be overwritten by v4Data anyway, but needed for immediate subtotal/tax calc)
      if (methodToUse === "separate_ledger" && gstBasis === "exclusive") {
          newFAmt = exAmt + (exAmt * rate / 100);
          newFRate = exRate + (exRate * rate / 100);
      } else if (methodToUse === "separate_ledger" && gstBasis === "inclusive") {
          newExAmt = fAmt / (1 + rate / 100);
          newExRate = fRate / (1 + rate / 100);
          newFAmt = fAmt;
          newFRate = fRate;
      } else if (methodToUse === "included_in_rate" && gstBasis === "exclusive") {
          newFAmt = exAmt + (exAmt * rate / 100);
          newFRate = exRate + (exRate * rate / 100);
      } else if (methodToUse === "included_in_rate" && gstBasis === "inclusive") {
          newExAmt = fAmt / (1 + rate / 100);
          newExRate = fRate / (1 + rate / 100);
          newFAmt = fAmt;
          newFRate = fRate;
      }
      
      return { 
        ...item, 
        amount: parseFloat(newExAmt.toFixed(2)), 
        rate: parseFloat(newExRate.toFixed(2)), 
        final_amount: parseFloat(newFAmt.toFixed(2)), 
        final_rate: parseFloat(newFRate.toFixed(2)) 
      };
    });

    const subtotal = updatedItems.reduce((sum, item) => sum + (Number(item.amount) || 0), 0);
    const totalTax = subtotal * (rate / 100);
    let newCgst = 0, newSgst = 0, newIgst = 0;
    if (newTaxType === 'interstate') {
      newIgst = parseFloat(totalTax.toFixed(2));
    } else {
      newCgst = parseFloat((totalTax / 2).toFixed(2));
      newSgst = parseFloat((totalTax / 2).toFixed(2));
    }
    const preRoundTotal = subtotal + newCgst + newSgst + newIgst;
    const roundedTotal = Math.round(preRoundTotal);
    const rounding = parseFloat((roundedTotal - preRoundTotal).toFixed(2));

    setInvoice(prev => ({ ...prev, gst_rate: newRate, tax_type: newTaxType, cgst: newCgst, sgst: newSgst, igst: newIgst, rounding_off: rounding }));
    if (newGstMethod) {
      setV4Config(prev => ({ ...prev, gst_recording_method: newGstMethod }));
    }
  };

  const getSupplierStatus = (name) => {
    if (!name) return null;
    const norm = (str) => {
      if (!str) return "";
      return str.toLowerCase().replace(/\s+/g, ' ').replace(/\s*\(\s*/g, '(').replace(/\s*\)\s*/g, ')').trim();
    };
    const targetNorm = norm(name);

    // Check confirmed
    if (meta.suppliers?.some(s => norm(s) === targetNorm)) {
      return { status: "in_tally", text: "✓ In Tally", cls: "text-green-600", borderCls: "border-green-300 focus:ring-green-500/50", bgCls: "bg-green-50 dark:bg-green-900/10" };
    }

    // Check masterStates
    if (meta.master_states?.ledgers) {
      const pendingMatch = meta.master_states.ledgers.find(m => norm(m.normalized_name) === targetNorm || norm(m.name) === targetNorm);
      if (pendingMatch) {
        if (pendingMatch.state === "syncing") return { status: "syncing", text: "↻ Syncing", cls: "text-blue-600", borderCls: "border-blue-300 focus:ring-blue-500/50", bgCls: "bg-blue-50 dark:bg-blue-900/10" };
        if (pendingMatch.state === "pending") return { status: "in_queue", text: "⏳ In Queue", cls: "text-amber-600", borderCls: "border-amber-300 focus:ring-amber-500/50", bgCls: "bg-amber-50 dark:bg-amber-900/10" };
        if (pendingMatch.state === "failed") return { status: "failed", text: "⚠ Supplier creation failed", cls: "text-red-600", borderCls: "border-red-300 focus:ring-red-500/50", bgCls: "bg-red-50 dark:bg-red-900/10", error: pendingMatch.error };
      }
    }

    // Not found
    return { status: "not_found", text: "⚠ New supplier — not found in Tally or Queue", cls: "text-red-600", borderCls: "border-red-300 focus:ring-red-500/50", bgCls: "bg-red-50 dark:bg-red-900/10" };
  };

  const addFilesToStaged = (newFiles, autoExtract = true, cropPoints = null) => {
    if (newFiles.length === 0) return;

    const newStaged = newFiles.filter(f => f.size > 0).map(f => ({
      file: f,
      previewUrl: URL.createObjectURL(f),
      cropPoints
    }));

    const combined = [...stagedFiles, ...newStaged];
    setStagedFiles(combined);

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }

    // Automatically trigger extraction on upload if requested
    if (autoExtract) {
      executeExtract(combined);
    }
  };

  const isHeicFile = (file) => {
    const name = (file.name || '').toLowerCase();
    const type = (file.type || '').toLowerCase();
    return name.endsWith('.heic') || name.endsWith('.heif') || type.includes('heic') || type.includes('heif');
  };

  const handleFileChange = (e) => {
    const picked = Array.from(e.target.files);
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (picked.length === 0) return;

    // Browsers can't decode HEIC into an <img> for the crop screen -- stage
    // those uncropped, exactly like before this feature, rather than
    // dropping the page when the crop UI fails to load it.
    const heicFiles = picked.filter(isHeicFile);
    const croppableFiles = picked.filter(f => !isHeicFile(f));

    const heicStaged = heicFiles.map(f => ({ file: f, previewUrl: URL.createObjectURL(f), cropPoints: null }));
    croppedBatchRef.current = heicStaged;

    if (croppableFiles.length > 0) {
      setFilesToCrop(croppableFiles);
    } else {
      const combined = [...stagedFiles, ...heicStaged];
      setStagedFiles(combined);
      executeExtract(combined);
    }
  };

  const handleCropQueueCapture = (file, points) => {
    const staged = { file, previewUrl: URL.createObjectURL(file), cropPoints: points };
    croppedBatchRef.current = [...croppedBatchRef.current, staged];

    const remaining = filesToCrop.slice(1);
    setFilesToCrop(remaining);

    if (remaining.length === 0) {
      const combined = [...stagedFiles, ...croppedBatchRef.current];
      setStagedFiles(combined);
      executeExtract(combined);
      croppedBatchRef.current = [];
    }
  };

  const handleCropQueueClose = () => {
    // Backed out mid-queue -- discard everything collected so far for this batch.
    croppedBatchRef.current = [];
    setFilesToCrop([]);
  };

  const removeStagedFile = (index) => {
    setStagedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleExtract = () => {
    if (stagedFiles.length === 0) return;
    executeExtract(stagedFiles);
  };

  const executeExtract = async (filesToExtract) => {
    const files = filesToExtract || stagedFiles;
    if (files.length === 0) return;

    // Use the first file for fallback preview/file refs if needed later
    setFile(files[0].file);
    setPreviewUrl(files[0].previewUrl);

    setIsExtracting(true);
    const fd = new FormData();
    files.forEach(sf => {
      fd.append("files", sf.file);
    });
    fd.append("crop_points", JSON.stringify(files.map(sf => sf.cropPoints || null)));

    try {
      const res = await fetch("/api/async-extract-proxy", { method: "POST", body: fd });
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.error || errorData.detail || "Extraction failed");
      }
      
      showToast("Invoice parsing started. Check Review Inbox in a few minutes.", "success");
      setIsExtracting(false);
      router.push('/review');
      
    } catch (error) {
      showToast(error.message, 'error');
      setIsExtracting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } 
  };

  const handleCreateItem = async (itemName, uom) => {
    const nameToCreate = (itemName || "").replace(/\s+/g, ' ').trim();
    if (!nameToCreate) return;

    const targetUom = (uom || "PCS").trim();
    const normUom = targetUom.toLowerCase();
    const uomState = meta.master_states?.uoms?.find(u => u.normalized_name === normUom);
    if (uomState && uomState.state === 'failed') {
      showToast(`Cannot create item because unit '${targetUom}' failed to sync: ${uomState.error || "Unknown error"}`, 'error');
      return;
    }

    setCreatingMaster({ type: 'ITEM', name: nameToCreate });
    try {
      const res = await fetch("/api/purchase-item/create-item", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nameToCreate, uom: uom || "PCS", parent_group: "Primary" })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to create item");
      showToast(`Created new Tally item: ${nameToCreate}`);

      // Re-fetch metadata instead of manual cache mutation
      await fetch("/api/purchase-item/metadata")
        .then(r => r.json())
        .then(d => setMeta(prev => ({ ...prev, ...d })))
        .catch(err => console.error(err));

      // Mark any line item whose raw name matches the newly created item as mapped
      setItems(prevItems =>
        prevItems.map(item =>
          item.name && item.name.replace(/\s+/g, ' ').trim().toLowerCase() === nameToCreate.toLowerCase()
            ? { ...item, mapped_name: data.name, uom: data.uom, is_mapped: true }
            : item
        )
      );
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setCreatingMaster(null);
    }
  };

  const handleItemMapChange = (index, mappedName, optionalUom) => {
    const newItems = [...items];
    newItems[index].mapped_name = mappedName;
    newItems[index].is_mapped = !!mappedName;
    if (forceManual) {
      newItems[index].name = mappedName;
    }
    
    let uomToSet = optionalUom;
    if (!uomToSet && mappedName) {
      const normalize = s => (s||"").replace(/\s+/g,' ').trim().toLowerCase();
      const normMapped = normalize(mappedName);
      const cacheEntry = Object.values(itemCache).find(v => normalize(v?.name) === normMapped);
      if (cacheEntry && cacheEntry.base_units) {
        uomToSet = cacheEntry.base_units;
      }
    }
    if (uomToSet) newItems[index].uom = uomToSet.toUpperCase();
    
    setItems(newItems);

    if (mappedName) {
      fetch("/api/purchase-item/save-alias", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ original_name: newItems[index].name, mapped_name: mappedName })
      }).catch(() => { });
    }
  };

  const updateItemVal = (index, field, value) => {
    if (v4RawData) {
      setV4RawData(prev => {
        if (!prev || !prev.extracted_data || !prev.extracted_data.items) return prev;
        const next = { ...prev };
        const items = [...next.extracted_data.items];
        const rawItem = { ...items[index] };
        
        if (field === 'name') rawItem.description = value;
        if (field === 'qty') {
            rawItem.quantity = value;
            rawItem._manual_qty = value;
        }
        if (field === 'uom') rawItem.unit = value;
        if (field === 'amount' || field === 'final_amount') {
            rawItem._manual_line_amount = value;
        }
        if (field === 'rate' || field === 'final_rate') {
            const qty = parseFloat(rawItem._manual_qty !== undefined ? rawItem._manual_qty : rawItem.quantity) || 1;
            rawItem._manual_line_amount = parseFloat(value) * qty;
        }
        
        items[index] = rawItem;
        next.extracted_data.items = items;
        return next;
      });
      return;
    }

    const newItems = [...items];
    const isGstInc = forceManual && v4Config?.gst_recording_method === 'included_in_rate';
    const gstRate = invoice?.gst_rate ? parseFloat(invoice?.gst_rate) : 0;

    if (field === 'qty') {
      newItems[index].qty = value;
      const qty = parseFloat(value) || 0;
      if (isGstInc) {
        const fRate = parseFloat(newItems[index].final_rate) || 0;
        newItems[index].final_amount = parseFloat((qty * fRate).toFixed(2));
        newItems[index].amount = parseFloat((newItems[index].final_amount / (1 + (gstRate / 100))).toFixed(2));
        newItems[index].rate = parseFloat((fRate / (1 + (gstRate / 100))).toFixed(2));
      } else {
        const rate = parseFloat(newItems[index].rate) || 0;
        newItems[index].amount = parseFloat((qty * rate).toFixed(2));
        newItems[index].final_amount = newItems[index].amount;
        newItems[index].final_rate = newItems[index].rate;
      }
    } else if (field === 'rate') {
      newItems[index].rate = value;
      const qty = parseFloat(newItems[index].qty) || 0;
      const rate = parseFloat(value) || 0;
      newItems[index].amount = parseFloat((qty * rate).toFixed(2));
      newItems[index].final_amount = newItems[index].amount;
      newItems[index].final_rate = newItems[index].rate;
    } else if (field === 'amount') {
      newItems[index].amount = value;
      newItems[index].final_amount = value;
    } else if (field === 'final_rate') {
      newItems[index].final_rate = value;
      const qty = parseFloat(newItems[index].qty) || 0;
      const fRate = parseFloat(value) || 0;
      newItems[index].final_amount = parseFloat((qty * fRate).toFixed(2));
      newItems[index].amount = parseFloat((newItems[index].final_amount / (1 + (gstRate / 100))).toFixed(2));
      newItems[index].rate = parseFloat((fRate / (1 + (gstRate / 100))).toFixed(2));
    } else if (field === 'final_amount') {
      newItems[index].final_amount = value;
      const fAmt = parseFloat(value) || 0;
      newItems[index].amount = parseFloat((fAmt / (1 + (gstRate / 100))).toFixed(2));
    } else {
      newItems[index][field] = value;
    }
    setItems(newItems);
  };

  const removeItem = (idx) => {
    const newItems = [...items];
    newItems.splice(idx, 1);
    setItems(newItems);
    if (editingItemIdx === idx) setEditingItemIdx(null);

    if (v4RawData) {
      setV4RawData(prev => {
        if (!prev || !prev.extracted_data || !prev.extracted_data.items) return prev;
        const rawItems = [...prev.extracted_data.items];
        rawItems.splice(idx, 1);
        return { ...prev, extracted_data: { ...prev.extracted_data, items: rawItems } };
      });
    }
  };

  const handlePost = async () => {
    const normalizeStr = (str) => (str || "").replace(/\s+/g, ' ').trim().toLowerCase();

    const getItemState = (mappedName) => {
      const norm = normalizeStr(mappedName);
      if (!norm) return null;

      const isConfirmedOrLocal = Object.keys(itemCache).some(key => normalizeStr(key) === norm) ||
        Object.values(itemCache).some(v => normalizeStr(v?.name) === norm) ||
        meta.stock_items.some(s => normalizeStr(s) === norm);
      if (isConfirmedOrLocal) return { state: 'confirmed' };

      const pState = meta.master_states?.stock_items?.find(m => normalizeStr(m.normalized_name) === norm);
      if (pState) return pState;
      return null;
    };

    const hasUnknownItems = items.some(item => {
      if (!item.mapped_name || item.mapped_name.trim() === "") return true;
      const stateObj = getItemState(item.mapped_name);
      return !stateObj && !item.is_mapped;
    });

    const hasFailedItems = items.some(item => {
      const stateObj = getItemState(item.mapped_name);
      return stateObj && stateObj.state === 'failed';
    });

    if (hasUnknownItems) {
      showToast("Please map all items to existing Tally items before pushing.", 'error');
      return;
    }

    if (hasFailedItems) {
      showToast("One or more items have failed to sync to Tally. Please resolve them first.", 'error');
      return;
    }

    if (adjustment.enabled && adjustment.items.length > 0 && !adjustment.store) {
      showToast("Please select a return cost centre for the adjustment.", 'error');
      return;
    }

    if (adjustment.enabled && adjustment.items.some(i => i.rateStatus === 'error' || !(Number(i.rate) > 0))) {
      showToast("Resolve the purchase rate for all return items before pushing.", 'error');
      return;
    }

    setIsPosting(true);
    try {
      let tallyDate = invoice.date || new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0];
      
      const isIncludedInRate = v4Data?.calculated_data?.gst_treatment === "included_in_rate";
      const payloadItems = items.map(item => ({
        ...item,
        amount: (isIncludedInRate && item.final_amount !== undefined) ? item.final_amount : item.amount,
        rate: (isIncludedInRate && item.final_rate !== undefined) ? item.final_rate : item.rate
      }));

      const postPayload = { 
        ...invoice, 
        tally_date: tallyDate, 
        items: payloadItems,
        cgst: isIncludedInRate ? 0 : invoice.cgst,
        sgst: isIncludedInRate ? 0 : invoice.sgst,
        igst: isIncludedInRate ? 0 : invoice.igst
      };
      if (adjustment.enabled && adjustment.items.length > 0) {
        postPayload.adjustment = {
          store: adjustment.store,
          reason: adjustment.reason || "",
          notes: adjustment.notes || "",
          items: adjustment.items.map(i => ({
            name: i.name,
            uom: i.uom,
            qty: Number(i.qty),
            rate: Number(i.rate),
            amount: Number(i.qty) * Number(i.rate)
          }))
        };
      }

      const res = await fetch("/api/purchase-item/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(postPayload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Post failed");

      if (data.status === 'queued') {
        if (data.reason === 'pending_master_dependency') {
          showToast("Invoice saved — waiting for supplier/item to sync to Tally.");
        } else {
          showToast("Invoice saved — it will sync when Tally is available.");
        }
      } else {
        showToast("Purchase saved to Tally");
      }

      if (data.adjustment?.status === 'failed') {
        showToast(`Purchase posted, but the return failed: ${data.adjustment.message}`, 'error');
      } else if (data.adjustment?.status === 'queued' && data.adjustment.message) {
        showToast(data.adjustment.message);
      }

      // If we posted from a draft, delete the draft now
      if (activeDraftId) {
        try {
          await fetch(`/api/purchase-drafts/${activeDraftId}`, { method: 'DELETE' });
        } catch (e) {
          console.error("Failed to delete draft after posting", e);
        }
      }
      
      if (onPostSuccess) onPostSuccess();
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setIsPosting(false);
    }
  };

  const handleCreateSupplier = async (supplierName) => {
    setCreatingMaster({ type: 'LEDGER', name: supplierName });
    try {
      const res = await fetch("/api/purchase-item/create-supplier", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: supplierName })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to create supplier");

      // Update local invoice form to have this supplier
      setInvoice(prev => ({ ...prev, supplier: supplierName }));

      // Re-fetch metadata
      fetch("/api/purchase-item/metadata")
        .then(r => r.json())
        .then(d => setMeta(prev => ({ ...prev, ...d })))
        .catch(err => console.error(err));

    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setCreatingMaster(null);
    }
  };

  const initializeManualDraft = () => {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    
    setInvoice({
      date: `${yyyy}-${mm}-${dd}`,
      voucher_number: "",
      supplier_name: "",
      store: "Mahagun",
      gst_rate: 0,
      tax_type: "local",
      cgst: 0,
      sgst: 0,
      igst: 0,
      rounding_off: 0
    });
    setItems([]);
    setV4RawData(null);
    setV4Config({
      selected_amount_header: null,
      gst_basis_override: null,
      gst_recording_method: "included_in_rate" // Keep existing default
    });
    setTotals({ printed: 0, calculated: 0, diff: 0 });
    setValidationStatus("matched");
    setForceManual(true);
  };

  if (!invoice) {
    return (
      <>
      <div className="space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="flex flex-col items-center justify-center p-8 text-center bg-gray-50 dark:bg-gray-800/50 rounded-2xl border-2 border-dashed border-gray-200 dark:border-gray-700 relative hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
            <input type="file" multiple accept="image/*,.heic,.heif,image/heic,image/heif" className="hidden" id="file-upload" onChange={handleFileChange} ref={fileInputRef} />
            <div onClick={() => !isExtracting && setShowUploadOptions(true)} className={`cursor-pointer flex flex-col items-center w-full h-full ${isExtracting ? 'opacity-50 cursor-not-allowed' : ''}`}>
              {isExtracting ? (
                <div className="w-12 h-12 rounded-full border-4 border-teal-200 border-t-teal-600 animate-spin mb-4" />
              ) : (
                <UploadCloud className="h-12 w-12 text-teal-600 mb-4" />
              )}
              <span className="text-lg font-bold text-gray-900 dark:text-gray-100">
                {isExtracting ? "AI is reading invoice..." : (stagedFiles.length > 0 ? "Add more pages" : "Upload / Take Invoice")}
              </span>
            </div>
          </div>
          
          <div 
            onClick={initializeManualDraft}
            className={`flex flex-col items-center justify-center p-8 text-center bg-gray-50 dark:bg-gray-800/50 rounded-2xl border-2 border-dashed border-gray-200 dark:border-gray-700 relative transition-colors ${isExtracting || stagedFiles.length > 0 ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800'}`}
          >
             <Edit3 className="h-12 w-12 text-teal-600 mb-4" />
             <span className="text-lg font-bold text-gray-900 dark:text-gray-100">
               Enter Purchase Manually
             </span>
          </div>
        </div>

        {stagedFiles.length > 0 && !isExtracting && (
          <div className="flex flex-col gap-4">
            <h3 className="text-sm font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">Staged Pages ({stagedFiles.length})</h3>
            <div className="flex gap-4 overflow-x-auto pb-2">
              {stagedFiles.map((sf, idx) => (
                <div key={idx} className="relative flex-shrink-0 w-24 h-32 border rounded-lg overflow-hidden group">
                  {sf.file.type.includes("pdf") ? (
                    <div className="w-full h-full flex items-center justify-center bg-gray-100 dark:bg-gray-700 text-xs font-bold text-gray-500">PDF</div>
                  ) : (
                    <img src={sf.previewUrl} alt={`Page ${idx + 1}`} className="w-full h-full object-cover" />
                  )}
                  <button onClick={() => removeStagedFile(idx)} className="absolute top-1 right-1 bg-red-500 text-white rounded-full w-6 h-6 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                    ×
                  </button>
                  <div className="absolute bottom-0 inset-x-0 bg-black/50 text-white text-[10px] p-1 text-center font-bold">Page {idx + 1}</div>
                </div>
              ))}
            </div>


            <div className="flex flex-col sm:flex-row gap-4">
              <Button 
                onClick={() => {
                  if (isAndroid && nativeCameraInputRef.current) {
                    nativeCameraInputRef.current.click();
                  } else {
                    setShowCamera(true);
                  }
                }} 
                variant="secondary" 
                className="flex-1"
              >
                + Add Another Page
              </Button>
              <Button onClick={handleExtract} disabled={isExtracting} className="flex-1">
                Done & Process Invoice
              </Button>
            </div>
          </div>
        )}


      </div>

      {showUploadOptions && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-white dark:bg-gray-900 rounded-2xl w-full max-w-sm p-6 flex flex-col gap-3 shadow-xl">
            <h3 className="text-xl font-bold text-gray-900 dark:text-white mb-2">Upload Invoice</h3>
            <Button 
              className="h-14 text-lg justify-start px-6 rounded-xl" 
              onClick={() => { 
                setShowUploadOptions(false); 
                if (isAndroid && nativeCameraInputRef.current) {
                  nativeCameraInputRef.current.click();
                } else {
                  setShowCamera(true); 
                }
              }}
            >
              <Camera className="w-6 h-6 mr-3" /> Take Photo
            </Button>
            <Button variant="secondary" className="h-14 text-lg justify-start px-6 rounded-xl border-2" onClick={() => { setShowUploadOptions(false); fileInputRef.current.click(); }}>
              <ImageIcon className="w-6 h-6 mr-3 text-gray-500" /> Choose from Photos
            </Button>
            <Button variant="ghost" className="mt-2 text-gray-500" onClick={() => setShowUploadOptions(false)}>Cancel</Button>
          </div>
        </div>
      )}

      {filesToCrop.length > 0 && (
        <CameraCapture
          initialPhotoFile={filesToCrop[0]}
          onCapture={handleCropQueueCapture}
          onClose={handleCropQueueClose}
        />
      )}

      {showCamera && !isAndroid && (
        <CameraCapture
          onCapture={(file, points) => {
            setShowCamera(false);
            addFilesToStaged([file], false, points);
          }}
          onClose={() => setShowCamera(false)}
        />
      )}

      {nativePhotoFile && isAndroid && (
        <CameraCapture
          initialPhotoFile={nativePhotoFile}
          onCapture={(file, points) => {
            setNativePhotoFile(null);
            addFilesToStaged([file], false, points);
          }}
          onClose={() => setNativePhotoFile(null)}
          onRetake={() => {
            setNativePhotoFile(null);
            if (nativeCameraInputRef.current) nativeCameraInputRef.current.click();
          }}
        />
      )}

      <input
        type="file"
        accept="image/*"
        capture="environment"
        ref={nativeCameraInputRef}
        className="hidden"
        onChange={(e) => {
          if (e.target.files && e.target.files.length > 0) {
            setNativePhotoFile(e.target.files[0]);
          }
          // Reset so selecting the same file works
          e.target.value = null;
        }}
      />

    </>
    );
  }

  // Totals calculations
  let itemSubtotal = 0;
  let computedCgst = invoice?.cgst || 0;
  let computedSgst = invoice?.sgst || 0;
  let computedIgst = invoice?.igst || 0;
  let computedRounding = invoice?.rounding_off || 0;
  let grandTotal = 0;

  const isGstIncluded = (v4Data?.calculated_data?.gst_treatment === 'included_in_rate') || (forceManual && v4Config?.gst_recording_method === 'included_in_rate');

  if (isGstIncluded) {
    itemSubtotal = items.reduce((acc, curr) => acc + (curr.final_amount !== undefined ? curr.final_amount : (curr.amount || 0)), 0);
    grandTotal = itemSubtotal + computedRounding;
  } else {
    itemSubtotal = items.reduce((acc, curr) => acc + (curr.amount || 0), 0);
    grandTotal = itemSubtotal + computedCgst + computedSgst + computedIgst + computedRounding;
  }

  const handleV4ConfigChange = (updates) => {
    setV4Config(prev => ({ ...prev, ...updates }));
  };

  const handleReject = () => {
    if (!activeDraftId) return;
    
    showConfirmDialog({
      title: "Reject Invoice",
      message: "Are you sure you want to discard this invoice? This action cannot be undone.",
      danger: true,
      onConfirm: async () => {
        setIsRejecting(true);
        try {
          const res = await fetch(`/api/purchase-drafts/${activeDraftId}`, { method: 'DELETE' });
          if (!res.ok) throw new Error("Failed to delete draft");
          
          showToast("Invoice discarded successfully.", "success");
          router.push('/review');
        } catch (err) {
          showToast(err.message || "Failed to reject invoice", "error");
          setIsRejecting(false);
        }
      }
    });
  };

  return (
    <div className="space-y-6">

      {v4Data && (
        <Card className="flex flex-col gap-6 !border-teal-500/30 dark:!border-teal-500/20 bg-gradient-to-br from-teal-50/50 to-white dark:from-teal-900/10 dark:to-gray-900 shadow-sm relative overflow-hidden">
          <div className="absolute top-0 right-0 p-4 opacity-10">
            <AlertCircle className="w-24 h-24 text-teal-600" />
          </div>

          <div className="relative z-10 flex flex-col gap-1 text-teal-900 dark:text-teal-100">
            <h2 className="text-xl font-bold tracking-tight">Invoice Configuration</h2>
            <p className="text-sm opacity-80">Confirm the invoice layout so we can calculate GST correctly.</p>
          </div>

          <div className="relative z-10 flex flex-col gap-6 divide-y divide-teal-100 dark:divide-gray-800">

            {/* Question 1: Amount Column Selection */}
            <div className="flex flex-col gap-3 pt-6 first:pt-0">
              <label className="text-sm font-bold text-gray-900 dark:text-gray-100 flex flex-col">
                <span className="flex items-center gap-1.5"><span className="w-5 h-5 rounded-full bg-teal-100 dark:bg-teal-900/50 text-teal-600 dark:text-teal-400 flex items-center justify-center text-xs">1</span> Amount column</span>
              </label>
              <div className="relative group max-w-md">
                <select
                  className="w-full appearance-none bg-white dark:bg-gray-800 border-2 border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 text-sm rounded-xl px-4 py-3 pr-10 outline-none focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10 transition-all font-medium cursor-pointer"
                  value={v4Data.calculated_data?.active_amount_header || ""}
                  onChange={e => handleV4ConfigChange({ selected_amount_header: e.target.value })}
                >
                  {v4Data.calculated_data?.available_amount_headers?.map(header => (
                    <option key={header} value={header}>{header}</option>
                  ))}
                  {(!v4Data.calculated_data?.available_amount_headers || v4Data.calculated_data.available_amount_headers.length === 0) && (
                    <option value="">No columns found</option>
                  )}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none group-hover:text-teal-500 transition-colors" />
              </div>
            </div>

            {/* Question 2: GST Basis */}
            <div className="flex flex-col gap-3 pt-6">
              <label className="text-sm font-bold text-gray-900 dark:text-gray-100 flex flex-col">
                <span className="flex items-center gap-1.5"><span className="w-5 h-5 rounded-full bg-teal-100 dark:bg-teal-900/50 text-teal-600 dark:text-teal-400 flex items-center justify-center text-xs">2</span> Does this amount include GST?</span>
              </label>
              <div className="flex flex-col gap-2 max-w-md">
                <label className={`flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${v4Data.calculated_data?.gst_basis === "inclusive" ? 'border-teal-500 bg-teal-50/50 dark:bg-teal-900/20' : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600'}`}>
                  <input type="radio" name="basis" value="inclusive" className="text-teal-600 focus:ring-teal-500 w-4 h-4" checked={v4Data.calculated_data?.gst_basis === "inclusive"} onChange={e => handleV4ConfigChange({ gst_basis_override: e.target.value })} />
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Yes, GST included</span>
                </label>
                <label className={`flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${v4Data.calculated_data?.gst_basis === "exclusive" ? 'border-teal-500 bg-teal-50/50 dark:bg-teal-900/20' : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600'}`}>
                  <input type="radio" name="basis" value="exclusive" className="text-teal-600 focus:ring-teal-500 w-4 h-4" checked={v4Data.calculated_data?.gst_basis === "exclusive"} onChange={e => handleV4ConfigChange({ gst_basis_override: e.target.value })} />
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">No, GST excluded</span>
                </label>
              </div>
            </div>

            {/* Question 3: Accounting Treatment */}
            <div className="flex flex-col gap-3 pt-6">
              <label className="text-sm font-bold text-gray-900 dark:text-gray-100 flex flex-col">
                <span className="flex items-center gap-1.5"><span className="w-5 h-5 rounded-full bg-teal-100 dark:bg-teal-900/50 text-teal-600 dark:text-teal-400 flex items-center justify-center text-xs">3</span> Purchase rate treatment</span>
              </label>
              <div className="flex flex-col gap-2 max-w-md">
                <label className={`flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${v4Data.calculated_data?.gst_treatment === "included_in_rate" ? 'border-teal-500 bg-teal-50/50 dark:bg-teal-900/20' : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600'}`}>
                  <input type="radio" name="treatment" value="included_in_rate" className="text-teal-600 focus:ring-teal-500 w-4 h-4" checked={v4Data.calculated_data?.gst_treatment === "included_in_rate"} onChange={e => handleV4ConfigChange({ gst_recording_method: e.target.value })} />
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Include GST in purchase rate</span>
                </label>
                <label className={`flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${v4Data.calculated_data?.gst_treatment === "separate_ledger" ? 'border-teal-500 bg-teal-50/50 dark:bg-teal-900/20' : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600'}`}>
                  <input type="radio" name="treatment" value="separate_ledger" className="text-teal-600 focus:ring-teal-500 w-4 h-4" checked={v4Data.calculated_data?.gst_treatment === "separate_ledger"} onChange={e => handleV4ConfigChange({ gst_recording_method: e.target.value })} />
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Record GST separately</span>
                </label>
              </div>
            </div>

          </div>
        </Card>
      )}

      <Card className="flex flex-col gap-4">
        <div className="flex gap-4">
          <Input
            label="Invoice Date"
            type="date"
            value={invoice.date || ""}
            onChange={e => setInvoice({ ...invoice, date: e.target.value })}
            className="flex-1"
          />
          <Input
            label="Invoice No."
            type="text"
            value={invoice.invoice_number || ""}
            onChange={e => setInvoice({ ...invoice, invoice_number: e.target.value })}
            className="flex-1"
          />
        </div>
        {(() => {
          const supplierStatus = getSupplierStatus(invoice.supplier);
          return (
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-gray-700 dark:text-gray-300 ml-1">Supplier</label>
              <MasterAutocomplete
                value={invoice.supplier || ""}
                onChange={val => setInvoice({ ...invoice, supplier: val })}
                placeholder="Select or type supplier..."
                confirmed={meta.suppliers}
                masterStates={meta.master_states?.ledgers}
                onCreate={handleCreateSupplier}
                inputClassName={supplierStatus ? `${supplierStatus.borderCls} ${supplierStatus.bgCls} text-gray-900 dark:text-gray-100` : ""}
                createLabel="supplier"
                isCreating={creatingMaster?.type === 'LEDGER'}
              />
              {supplierStatus && (
                <div className={`text-xs font-bold ml-1 flex flex-col ${supplierStatus.cls}`}>
                  <span>{supplierStatus.text}</span>
                  {supplierStatus.error && <span className="font-normal opacity-80 mt-0.5">{supplierStatus.error}</span>}
                </div>
              )}
            </div>
          );
        })()}
        <Select
          label="Store"
          value={invoice?.cost_center || ""}
          onChange={e => setInvoice({ ...invoice, cost_center: e.target.value })}
          disabled={!!lockedStore}
        >
          <option value="" disabled>Select Store...</option>
          {meta.stores.map(s => <option key={s} value={s}>{s}</option>)}
        </Select>

        {forceManual && (
          <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-200 dark:border-gray-700 mt-2">
            <span className="text-sm font-bold text-gray-700 dark:text-gray-300">GST Inclusive Rate</span>
            <label className="relative inline-flex items-center cursor-pointer">
              <input 
                type="checkbox" 
                className="sr-only peer" 
                checked={v4Config?.gst_recording_method !== 'exclude_gst'} 
                onChange={e => handleTaxRecalculation(invoice?.gst_rate, invoice.tax_type, e.target.checked ? "included_in_rate" : "exclude_gst")} 
              />
              <div className="w-11 h-6 bg-gray-300 peer-focus:outline-none rounded-full peer dark:bg-gray-600 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-teal-600"></div>
            </label>
          </div>
        )}

        {previewUrl && (
          <a href={previewUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 text-teal-600 font-medium text-sm pt-2 border-t border-gray-100 dark:border-gray-800">
            <ImageIcon className="w-4 h-4" />
            View original photo
          </a>
        )}
      </Card>

        <>
          <div className="space-y-3">
            <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase px-1">{items.length} items</h3>

            <div className="flex flex-col gap-3">
              {items.map((item, idx) => {
                const normalizeStr = (str) => (str || "").replace(/\s+/g, ' ').trim().toLowerCase();

                const itemStateObj = (() => {
                  const norm = normalizeStr(item.mapped_name);
                  if (!norm) return null;
                  const isConfirmedOrLocal = Object.keys(itemCache).some(key => normalizeStr(key) === norm) ||
                    Object.values(itemCache).some(v => normalizeStr(v?.name) === norm) ||
                    meta.stock_items.some(s => normalizeStr(s) === norm);
                  if (isConfirmedOrLocal) return { state: 'confirmed' };
                  const pState = meta.master_states?.stock_items?.find(m => normalizeStr(m.normalized_name) === norm);
                  return pState || null;
                })();

                const isKnownItem = !!itemStateObj || item.is_mapped;
                const isEditing = editingItemIdx === idx;

                return (
                  <Card
                    key={idx}
                    onClick={() => !isEditing && setEditingItemIdx(idx)}
                    className={`flex flex-col gap-2 relative ${isEditing ? 'ring-2 ring-teal-500/50' : 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/80'}`}
                  >
                    {!isEditing ? (
                      <>
                        <div className="flex justify-between items-start">
                          <p className="text-base font-bold text-gray-900 dark:text-gray-100 leading-tight pr-4">{item.name}</p>
                          <div className="flex items-center gap-1 -mt-1 -mr-2">
                            <p className="text-base font-bold text-gray-900 dark:text-gray-100 whitespace-nowrap mr-2">
                              ₹ {isGstIncluded && item.final_amount !== undefined ? Number(item.final_amount || 0).toFixed(2) : Number(item.amount || 0).toFixed(2)}
                            </p>
                            <ChevronDown className="w-5 h-5 text-gray-300" />
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); removeItem(idx); }}
                              className="text-gray-400 hover:text-red-600 p-2 rounded-full transition-colors focus:outline-none"
                              aria-label="Remove Item"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        </div>

                        <div className="flex justify-between items-center mt-1">
                          <div className="flex items-center gap-1.5">
                            {itemStateObj ? (
                              itemStateObj.state === 'failed' ? (
                                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-red-600 bg-red-50 dark:bg-red-900/30 px-2 py-1 rounded cursor-pointer" onClick={(e) => { e.stopPropagation(); alert(itemStateObj.error || "Item failed to sync."); }}>
                                  ⚠ Failed
                                </span>
                              ) : itemStateObj.state === 'pending' ? (
                                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-amber-600 bg-amber-50 dark:bg-amber-900/30 px-2 py-1 rounded">
                                  ⏳ Mapped • In Queue
                                </span>
                              ) : itemStateObj.state === 'syncing' ? (
                                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-blue-600 bg-blue-50 dark:bg-blue-900/30 px-2 py-1 rounded">
                                  ↻ Mapped • Syncing
                                </span>
                              ) : (
                                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-green-600 bg-green-50 dark:bg-green-900/30 px-2 py-1 rounded">
                                  ✓ Matched
                                </span>
                              )
                            ) : isKnownItem ? (
                              <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-green-600 bg-green-50 dark:bg-green-900/30 px-2 py-1 rounded">
                                ✓ Matched
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-amber-600 bg-amber-50 dark:bg-amber-900/30 px-2 py-1 rounded">
                                ⚠ Needs Match
                              </span>
                            )}
                          </div>
                          <p className="text-sm font-medium text-gray-500">
                            {item.qty} {item.uom} × ₹{isGstIncluded && item.final_rate !== undefined ? Number(item.final_rate || 0).toFixed(2) : Number(item.rate || 0).toFixed(2)}
                          </p>
                        </div>

                      </>
                    ) : (
                      <div className="flex flex-col gap-4">
                        <div className="flex justify-between items-center">
                          <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase">Edit Item</h4>
                          <div className="flex items-center gap-4">
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); removeItem(idx); }}
                              className="text-gray-400 hover:text-red-600 p-2 -my-2 rounded-full transition-colors focus:outline-none"
                              aria-label="Remove Item"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                            <button type="button" onClick={(e) => { e.stopPropagation(); setEditingItemIdx(null); }} className="text-teal-600 text-sm font-bold p-2 -my-2 -mr-2 focus:outline-none">Done</button>
                          </div>
                        </div>

                        {!forceManual && (
                          <div className="space-y-1.5">
                            <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Item Name (from invoice)</label>
                            <textarea
                              value={item.name}
                              onChange={e => updateItemVal(idx, 'name', e.target.value)}
                              rows={2}
                              className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500 resize-none"
                            />
                          </div>
                        )}

                        <div className="space-y-1.5">
                          <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">
                            {forceManual ? "Product" : "Tally Item Mapping"}
                          </label>
                          <MasterAutocomplete
                            placeholder={forceManual ? "Search or create product..." : "Select tally item..."}
                            rawItemName={item.name || ""}
                            confirmed={meta.stock_items}
                            masterStates={meta.master_states?.stock_items}
                            value={item.mapped_name || ""}
                            onChange={val => handleItemMapChange(idx, val)}
                            onCreate={(val) => handleCreateItem(val, items[editingItemIdx]?.uom || "PCS")}
                            isCreating={creatingMaster?.type === 'ITEM'}
                          />
                          {!isKnownItem && !forceManual && <span className="text-xs font-bold text-amber-600 ml-1">Needs Match</span>}
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                          <div className="space-y-1.5">
                            <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Quantity</label>
                            <input
                              type="number" step="0.01"
                              value={item.qty}
                              onChange={e => updateItemVal(idx, 'qty', e.target.value)}
                              className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500"
                            />
                          </div>
                          <div className="space-y-1.5">
                            <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">UOM</label>
                            <input
                              list="uoms-list"
                              value={item.uom || ""}
                              onChange={e => updateItemVal(idx, 'uom', e.target.value.toUpperCase())}
                              className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500 uppercase"
                            />
                          </div>
                          <div className="space-y-1.5">
                            <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">
                              {isGstIncluded && forceManual ? "Rate (Incl. GST) (₹)" : isGstIncluded ? "Rate (Excl. GST) (₹)" : "Rate (₹)"}
                            </label>
                            <input
                              type="number" step="0.01"
                              value={isGstIncluded && forceManual ? item.final_rate : item.rate}
                              onChange={e => updateItemVal(idx, isGstIncluded && forceManual ? 'final_rate' : 'rate', e.target.value)}
                              className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500"
                            />
                          </div>
                          <div className="space-y-1.5">
                            <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">
                              {isGstIncluded && forceManual ? "Amount (Incl. GST) (₹)" : isGstIncluded ? "Amount (Excl. GST) (₹)" : "Amount (₹)"}
                            </label>
                            <input
                              type="number" step="0.01"
                              value={isGstIncluded && forceManual ? item.final_amount : item.amount}
                              onChange={e => updateItemVal(idx, isGstIncluded && forceManual ? 'final_amount' : 'amount', parseFloat(e.target.value) || 0)}
                              className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-gray-900 dark:text-gray-100 outline-none font-bold"
                            />
                          </div>
                        </div>


                      </div>
                    )}
                  </Card>
                );
              })}
            </div>

            <datalist id="uoms-list">
              {meta.uoms.map(u => <option key={u} value={u} />)}
              <option value="PCS" /><option value="NOS" /><option value="KGS" /><option value="BOX" /><option value="PKT" />
            </datalist>

            <Button variant="secondary" onClick={() => {
              setItems([...items, { name: "", mapped_name: "", qty: 1, rate: 0, amount: 0, uom: "PCS" }]);
              setEditingItemIdx(items.length);
            }} className="mt-2 text-teal-600">
              <PlusCircle className="w-4 h-4" /> Add Item
            </Button>
          </div>

          <Card className="flex flex-col gap-3">
            <div className="flex justify-between items-center">
              <span className="font-bold text-gray-900 dark:text-white">Taxes & Totals</span>
            </div>

            <div className="space-y-3 pt-2">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">GST Rate (%)</label>
                  <input type="number" value={invoice?.gst_rate} onChange={e => handleTaxRecalculation(e.target.value, invoice.tax_type)} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Tax Type</label>
                  <select value={invoice.tax_type} onChange={e => handleTaxRecalculation(invoice?.gst_rate, e.target.value)} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none">
                    <option value="local">Local</option>
                    <option value="interstate">Interstate</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                {invoice.tax_type === 'local' ? (
                  <>
                    <div className="space-y-1.5"><label className="text-[10px] font-bold text-gray-500 uppercase">CGST</label><input type="number" step="0.01" value={invoice.cgst} onChange={e => setInvoice({ ...invoice, cgst: parseFloat(e.target.value) || 0 })} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none" /></div>
                    <div className="space-y-1.5"><label className="text-[10px] font-bold text-gray-500 uppercase">SGST</label><input type="number" step="0.01" value={invoice.sgst} onChange={e => setInvoice({ ...invoice, sgst: parseFloat(e.target.value) || 0 })} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none" /></div>
                  </>
                ) : (
                  <div className="space-y-1.5 col-span-2"><label className="text-[10px] font-bold text-gray-500 uppercase">IGST</label><input type="number" step="0.01" value={invoice.igst} onChange={e => setInvoice({ ...invoice, igst: parseFloat(e.target.value) || 0 })} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none" /></div>
                )}
              </div>
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Rounding</label>
                <input type="number" step="0.01" value={invoice.rounding_off} onChange={e => setInvoice({ ...invoice, rounding_off: parseFloat(e.target.value) || 0 })} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none" />
              </div>
            </div>

            <div className="border-t border-gray-100 dark:border-gray-800 mt-2">
              {isGstIncluded ? (
                <div className="flex flex-col gap-1 text-sm font-medium text-gray-500 mt-3">
                  <div className="flex justify-between">
                    <span>Subtotal / Taxable Value</span>
                    <span>₹ {itemSubtotal.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between text-teal-600 dark:text-teal-400">
                    <span>GST included in rates</span>
                    <span>₹ {(computedCgst + computedSgst + computedIgst).toFixed(2)}</span>
                  </div>
                  {invoice.rounding_off !== 0 && (
                    <div className="flex justify-between">
                      <span>Round Off</span>
                      <span>{invoice.rounding_off > 0 ? '+' : '-'}₹ {Math.abs(invoice.rounding_off).toFixed(2)}</span>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col gap-1 text-sm font-medium text-gray-500 mt-3">
                  <div className="flex justify-between">
                    <span>Subtotal</span>
                    <span>₹ {itemSubtotal.toFixed(2)}</span>
                  </div>
                  {invoice.tax_type === 'local' ? (
                    <>
                      <div className="flex justify-between">
                        <span>CGST {(invoice?.gst_rate / 2)}%</span>
                        <span>₹ {computedCgst.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>SGST {(invoice?.gst_rate / 2)}%</span>
                        <span>₹ {computedSgst.toFixed(2)}</span>
                      </div>
                    </>
                  ) : (
                    <div className="flex justify-between">
                      <span>IGST {invoice?.gst_rate}%</span>
                      <span>₹ {computedIgst.toFixed(2)}</span>
                    </div>
                  )}
                  {invoice.rounding_off !== 0 && (
                    <div className="flex justify-between">
                      <span>Round Off</span>
                      <span>{invoice.rounding_off > 0 ? '+' : '-'}₹ {Math.abs(invoice.rounding_off).toFixed(2)}</span>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="h-px bg-gray-100 dark:bg-gray-800 mt-2" />
            <div className="flex justify-between text-xl font-black text-gray-900 dark:text-white">
              <span>Total</span>
              <span>₹ {grandTotal.toFixed(2)}</span>
            </div>
          </Card>

          <Card className="flex flex-col gap-3">
            <div className="flex justify-between items-center cursor-pointer" onClick={() => setAdjustment(prev => ({ ...prev, enabled: !prev.enabled }))}>
              <div className="flex items-center gap-2">
                <input type="checkbox" checked={adjustment.enabled} onChange={() => { }} className="w-4 h-4 text-teal-600 focus:ring-teal-500 rounded cursor-pointer" />
                <span className="font-bold text-gray-900 dark:text-white">Supplier Adjustment / Return</span>
              </div>
            </div>

            {adjustment.enabled && (
              <div className="space-y-3 pt-3 border-t border-gray-100 dark:border-gray-800">
                <p className="text-xs text-gray-500 mb-2">Select the items being returned to this supplier. This will post a Debit Note (with stock effect) to Tally, using each item&apos;s latest purchase rate.</p>

                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Add Return Item</label>
                  <SearchableSelect
                    options={meta.stock_items.filter(n => !adjustment.items.some(i => i.name === n))}
                    value=""
                    onChange={handleAddReturnItem}
                    placeholder="Search item to add to return..."
                  />
                </div>

                {adjustment.items.length > 0 && (
                  <div className="space-y-2">
                    {adjustment.items.map((ri, idx) => (
                      <div key={ri.name} className="flex flex-col gap-2 bg-gray-50 dark:bg-gray-800 p-2 rounded-lg border border-gray-200 dark:border-gray-700">
                        <div className="flex items-center gap-2">
                          <span className="flex-1 text-sm font-medium text-gray-900 dark:text-gray-100 break-words" title={ri.name}>{ri.name}</span>
                          <button type="button" onClick={() => openHistoryPicker(idx, ri.name)} title="Pick a different rate from purchase history" className="text-teal-500 hover:text-teal-700 shrink-0">
                            <History className="w-4 h-4" />
                          </button>
                          <button type="button" onClick={() => handleRemoveReturnItem(idx)} className="text-red-400 hover:text-red-600 shrink-0">
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                        <div className="flex items-center gap-2">
                          <input
                            type="number" step="0.01" min="0" value={ri.qty}
                            onChange={e => handleReturnItemQtyChange(idx, e.target.value)}
                            className="w-16 p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 outline-none focus:border-teal-500"
                          />
                          <span className="text-[10px] text-gray-400 w-8">{ri.uom}</span>
                          <span className="flex-1 text-xs text-right">
                            {ri.rateStatus === 'loading' ? (
                              <span className="text-gray-400">Fetching...</span>
                            ) : ri.rateStatus === 'error' ? (
                              <span className="text-red-500 font-medium">No rate found</span>
                            ) : (
                              <span className="text-gray-500">₹{ri.rate} /unit</span>
                            )}
                          </span>
                          <span className="w-20 text-sm text-right font-bold text-gray-900 dark:text-gray-100">
                            ₹{((Number(ri.qty) || 0) * (Number(ri.rate) || 0)).toFixed(2)}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Return Cost Centre <span className="text-red-500">*</span></label>
                    <select value={adjustment.store} onChange={e => setAdjustment({ ...adjustment, store: e.target.value })} disabled={!!lockedStore} className={`w-full p-2 text-sm rounded border bg-white dark:bg-gray-800 outline-none focus:border-teal-500 ${!adjustment.store && adjustment.items.length > 0 ? 'border-red-500' : 'border-gray-200 dark:border-gray-700'}`}>
                      <option value="" disabled>Select Store...</option>
                      {meta.stores.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Reason (Optional)</label>
                    <input type="text" value={adjustment.reason} onChange={e => setAdjustment({ ...adjustment, reason: e.target.value })} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none focus:border-teal-500" placeholder="e.g. Damaged goods" />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Notes (Optional)</label>
                  <input type="text" value={adjustment.notes} onChange={e => setAdjustment({ ...adjustment, notes: e.target.value })} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none focus:border-teal-500" />
                </div>
                {returnTotal > 0 && (
                  <div className="flex justify-between items-center bg-gray-50 dark:bg-gray-800 p-3 rounded-lg mt-2 border border-gray-200 dark:border-gray-700">
                    <span className="font-bold text-gray-900 dark:text-gray-100 text-sm">Net Supplier Payable:</span>
                    <span className="font-black text-teal-600 dark:text-teal-400 text-lg">₹ {Math.max(0, grandTotal - returnTotal).toFixed(2)}</span>
                  </div>
                )}
              </div>
            )}
          </Card>

          {v4Data?.calculated_data?.gst_basis === "unknown" ? (
            <div className="bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800 p-4 rounded-xl text-amber-600 dark:text-amber-400 text-sm font-medium">
              <AlertCircle className="w-5 h-5 inline-block mr-2" />
              Please select whether the selected amount includes GST above to continue.
            </div>
          ) : (
            <div className="flex gap-3">
              <Button onClick={handleReject} variant="danger" className="flex-1" disabled={isPosting || isRejecting}>
                {isRejecting ? "Rejecting..." : "Reject"}
              </Button>
              <Button onClick={handlePost} className="flex-[2]" disabled={isPosting || isRejecting || !invoice.cost_center}>
                {isPosting ? "Sending..." : "Push to Tally"}
              </Button>
            </div>
          )}
        </>

      {historyPicker.open && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4" onClick={closeHistoryPicker}>
          <div className="bg-white dark:bg-gray-900 rounded-t-2xl sm:rounded-2xl w-full sm:max-w-lg max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-gray-100 dark:border-gray-800">
              <div>
                <h3 className="font-bold text-gray-900 dark:text-white">Purchase History</h3>
                <p className="text-xs text-gray-500 truncate max-w-[240px]">{historyPicker.itemName}</p>
              </div>
              <button onClick={closeHistoryPicker} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="overflow-y-auto flex-1 p-4">
              {historyPicker.loading && (
                <p className="text-sm text-gray-400 text-center py-8">Fetching purchase history...</p>
              )}
              {historyPicker.error && (
                <div className="text-center py-8">
                  <p className="text-sm text-red-500 mb-2">{historyPicker.error}</p>
                  <button onClick={() => openHistoryPicker(historyPicker.itemIdx, historyPicker.itemName)} className="text-sm text-teal-600 font-bold">Retry</button>
                </div>
              )}
              {!historyPicker.loading && !historyPicker.error && (
                <>
                  {historyPicker.source === 'local_cache' && (
                    <p className="text-xs bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 rounded-lg p-2 mb-3">
                      Showing last known rates — Tally is offline right now.
                    </p>
                  )}
                  {historyPicker.entries.length === 0 ? (
                    <p className="text-sm text-gray-400 text-center py-8">No purchase history in the last 2 years for this item.</p>
                  ) : (
                    <div className="space-y-2">
                      {historyPicker.entries.map((entry, eidx) => (
                        <button
                          key={eidx}
                          onClick={() => handleSelectHistoryRate(entry)}
                          className="w-full text-left p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-teal-500 hover:bg-teal-50/50 dark:hover:bg-teal-900/10 transition-colors"
                        >
                          <div className="flex justify-between items-center">
                            <span className="text-sm font-bold text-gray-900 dark:text-gray-100">{entry.date}</span>
                            <span className="text-lg font-black text-teal-600 dark:text-teal-400">₹{entry.rate}</span>
                          </div>
                          <div className="flex justify-between items-center mt-1 text-xs text-gray-500">
                            <span className="truncate max-w-[150px]">
                              {entry.origin === 'repack' ? 'Made in-house (Repack)' : (entry.supplier || 'Unknown supplier')}
                            </span>
                            <span>{entry.voucher_number || 'No voucher #'} · Qty {entry.qty ?? '—'} {entry.unit || ''}</span>
                          </div>
                          {entry.origin === 'app_post' && (
                            <span className="inline-block mt-1 text-[10px] font-bold uppercase tracking-wider text-amber-600 bg-amber-50 dark:bg-amber-900/30 px-2 py-0.5 rounded">
                              Pending Tally sync
                            </span>
                          )}
                          {entry.origin === 'repack' && (
                            <span className="inline-block mt-1 text-[10px] font-bold uppercase tracking-wider text-indigo-600 bg-indigo-50 dark:bg-indigo-900/30 px-2 py-0.5 rounded">
                              Repack cost
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
