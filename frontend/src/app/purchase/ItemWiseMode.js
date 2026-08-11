'use client';
import { useState, useEffect, useRef } from "react";
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { useUI } from '@/context/UIContext';
import { UploadCloud, Image as ImageIcon, AlertCircle, CheckCircle2, ChevronDown, ChevronRight, PlusCircle, Trash2 } from "lucide-react";

function MasterAutocomplete({ value, onChange, placeholder, confirmed = [], masterStates = [], onCreate, disabled, rawItemName = "", inputClassName = "", createLabel = "item", isCreating = false }) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [hasEditedSearch, setHasEditedSearch] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(-1);
  const wrapperRef = useRef(null);

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
      <input
        role="combobox"
        aria-expanded={isOpen}
        aria-controls="listbox"
        type="text"
        value={isOpen ? search : value}
        onChange={(e) => {
          setSearch(e.target.value);
          setHasEditedSearch(true);
          if (!isOpen) setIsOpen(true);
        }}
        onFocus={() => {
          if (!hasEditedSearch) {
            setSearch(value || rawItemName || "");
          }
          setIsOpen(true);
        }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        className={`w-full min-h-[48px] px-4 rounded-xl border outline-none transition-colors ${inputClassName || "border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-teal-500/50"}`}
        placeholder={placeholder}
      />
      
      {isOpen && (
        <div className="absolute z-50 w-full mt-2 bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-100 dark:border-gray-700 max-h-60 overflow-y-auto" role="listbox">
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
  const { showToast } = useUI();

  const [meta, setMeta] = useState({ suppliers: [], stock_items: [], uoms: [], stores: ["Mahagun", "Vvip", "Gulshan"] });
  const [itemCache, setItemCache] = useState({});

  const [file, setFile] = useState(null);
  const [isExtracting, setIsExtracting] = useState(false);
  const [invoice, setInvoice] = useState(null);
  const [items, setItems] = useState([]);
  const [editingItemIdx, setEditingItemIdx] = useState(null);
  const [showTaxEdit, setShowTaxEdit] = useState(false);

  const [isPosting, setIsPosting] = useState(false);
  const [creatingMaster, setCreatingMaster] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [stagedFiles, setStagedFiles] = useState([]);
  const fileInputRef = useRef(null);

  const [validationStatus, setValidationStatus] = useState("matched"); // "matched", "needs_mapping", "unverified"
  const [mappingFailed, setMappingFailed] = useState(false);
  const [detectedHeaders, setDetectedHeaders] = useState([]);
  const [totals, setTotals] = useState({ printed: 0, calculated: 0, diff: 0 });
  const [colMapping, setColMapping] = useState({
    qty_header: "",
    rate_header: "",
    rate_includes_gst: false,
    discount_header: "",
    discount_treatment: "ignore",
    amount_header: "",
    amount_includes_gst: false,
    uom_header: ""
  });
  const [forceManual, setForceManual] = useState(false);

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

  const handleTaxRecalculation = (newRate, newTaxType) => {
    const rate = Number(newRate);
    const subtotal = items.reduce((sum, item) => sum + (Number(item.amount) || 0), 0);
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

  const handleFileChange = (e) => {
    const newFiles = Array.from(e.target.files);
    if (newFiles.length === 0) return;
    
    setStagedFiles(prev => [
      ...prev,
      ...newFiles.filter(f => f.size > 0).map(f => ({
        file: f,
        previewUrl: URL.createObjectURL(f)
      }))
    ]);
    
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const removeStagedFile = (index) => {
    setStagedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleExtract = async () => {
    if (stagedFiles.length === 0) return;

    // Use the first file for fallback preview/file refs if needed later
    setFile(stagedFiles[0].file);
    setPreviewUrl(stagedFiles[0].previewUrl);

    setIsExtracting(true);
    const fd = new FormData();
    stagedFiles.forEach(sf => {
      fd.append("files", sf.file);
    });

    try {
      const res = await fetch("/api/purchase-item/extract", { method: "POST", body: fd });
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || "Extraction failed");
      }
      const data = await res.json();

      setInvoice({
        supplier: data.supplier,
        invoice_number: data.invoice_number,
        date: data.date,
        cost_center: "",
        cgst: data.cgst,
        sgst: data.sgst,
        igst: data.igst,
        rounding_off: data.rounding_off,
        gst_rate: data.gst_rate !== undefined ? data.gst_rate : 0,
        tax_type: data.tax_type || "local"
      });
      setItems(data.items);
      setValidationStatus(data.validation_status || "matched");
      setMappingFailed(data.mapping_attempt_failed || false);
      setDetectedHeaders(data.detected_headers || []);
      setTotals({
        printed: data.printed_grand_total || 0,
        calculated: data.calculated_grand_total || 0,
        diff: data.total_difference || 0
      });
      setForceManual(false);
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
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
    if (optionalUom) newItems[index].uom = optionalUom;
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
    const newItems = [...items];
    newItems[index][field] = value;

    if (field === 'qty' || field === 'rate') {
      const qty = parseFloat(newItems[index].qty) || 0;
      const rate = parseFloat(newItems[index].rate) || 0;
      newItems[index].amount = parseFloat((qty * rate).toFixed(2));
    }
    setItems(newItems);
  };

  const removeItem = (idx) => {
    const newItems = [...items];
    newItems.splice(idx, 1);
    setItems(newItems);
    if (editingItemIdx === idx) setEditingItemIdx(null);
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

    setIsPosting(true);
    try {
      let tallyDate = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0].replace(/-/g, '');
      if (invoice.date) tallyDate = invoice.date.replace(/-/g, '');

      const res = await fetch("/api/purchase-item/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...invoice, tally_date: tallyDate, items })
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
      const res = await fetch("/api/purchase/create-supplier", {
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

  const handleApplyMapping = async () => {
    setIsExtracting(true);
    const fd = new FormData();
    fd.append("file", file);
    
    const payload = { ...colMapping };
    if (!payload.discount_header) {
      payload.discount_treatment = "ignore";
    }

    fd.append("column_mapping", JSON.stringify(payload));
    try {
      const res = await fetch("/api/purchase-item/extract", { method: "POST", body: fd });
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || "Extraction failed");
      }
      const data = await res.json();
      
      setInvoice({
        supplier: data.supplier,
        invoice_number: data.invoice_number,
        date: data.date,
        cost_center: "",
        cgst: data.cgst,
        sgst: data.sgst,
        igst: data.igst,
        rounding_off: data.rounding_off,
        gst_rate: data.gst_rate !== undefined ? data.gst_rate : 0,
        tax_type: data.tax_type || "local"
      });
      setItems(data.items);
      setValidationStatus(data.validation_status || "matched");
      setMappingFailed(data.mapping_attempt_failed || false);
      setDetectedHeaders(data.detected_headers || []);
      setTotals({
        printed: data.printed_grand_total || 0,
        calculated: data.calculated_grand_total || 0,
        diff: data.total_difference || 0
      });
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setIsExtracting(false);
    }
  };

  if (!invoice) {
    return (
      <div className="space-y-6">
        <div className="flex flex-col items-center justify-center p-8 text-center bg-gray-50 dark:bg-gray-800/50 rounded-2xl border-2 border-dashed border-gray-200 dark:border-gray-700 relative">
          <input type="file" multiple accept="image/*,.pdf,.heic,.heif,image/heic,image/heif" className="hidden" id="file-upload" onChange={handleFileChange} ref={fileInputRef} />
          <label htmlFor="file-upload" className="cursor-pointer flex flex-col items-center">
            {isExtracting ? (
              <div className="w-12 h-12 rounded-full border-4 border-teal-200 border-t-teal-600 animate-spin mb-4" />
            ) : (
              <UploadCloud className="h-12 w-12 text-teal-600 mb-4" />
            )}
            <span className="text-lg font-bold text-gray-900 dark:text-gray-100">
              {isExtracting ? "AI is reading invoice..." : (stagedFiles.length > 0 ? "Add more pages" : "Take a photo of the invoice")}
            </span>
          </label>
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
            
            <Button onClick={handleExtract} disabled={isExtracting} className="w-full">
              Extract Invoice ({stagedFiles.length} pages)
            </Button>
          </div>
        )}
      </div>
    );
  }

  const itemSubtotal = items.reduce((acc, curr) => acc + (curr.amount || 0), 0);
  const grandTotal = itemSubtotal + (invoice.cgst || 0) + (invoice.sgst || 0) + (invoice.igst || 0) + (invoice.rounding_off || 0);

  return (
    <div className="space-y-6">

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
          value={invoice.cost_center}
          onChange={e => setInvoice({ ...invoice, cost_center: e.target.value })}
        >
          <option value="" disabled>Select Store...</option>
          {meta.stores.map(s => <option key={s} value={s}>{s}</option>)}
        </Select>

        {previewUrl && (
          <a href={previewUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 text-teal-600 font-medium text-sm pt-2 border-t border-gray-100 dark:border-gray-800">
            <ImageIcon className="w-4 h-4" />
            View original photo
          </a>
        )}
      </Card>

      {validationStatus === "needs_mapping" && !forceManual ? (
        <Card className="flex flex-col gap-4 border-amber-200 bg-amber-50 dark:bg-amber-900/10">
          <div className="flex items-center gap-2 text-amber-800 dark:text-amber-200">
            <AlertCircle className="w-5 h-5" />
            <h3 className="font-bold text-lg">Invoice total doesn't match</h3>
          </div>
          
          <div className="grid grid-cols-3 gap-2 text-sm bg-white dark:bg-gray-800 p-3 rounded-lg border border-amber-100 dark:border-gray-700">
            <div><span className="text-gray-500">Printed:</span> <span className="font-bold">₹{totals.printed.toFixed(2)}</span></div>
            <div><span className="text-gray-500">Extracted:</span> <span className="font-bold">₹{totals.calculated.toFixed(2)}</span></div>
            <div><span className="text-gray-500">Difference:</span> <span className="font-bold text-red-500">₹{totals.diff.toFixed(2)}</span></div>
          </div>

          {mappingFailed && (
            <p className="text-sm text-red-600 font-medium bg-red-50 dark:bg-red-900/20 p-2 rounded">
              The selected columns still don't match the invoice total. Check the selections or continue and edit the items manually.
            </p>
          )}

          <p className="text-sm font-medium text-amber-900 dark:text-amber-100 pt-2">Please identify these invoice columns:</p>
          
          <div className="grid grid-cols-2 gap-4">
            <Select label="Quantity" value={colMapping.qty_header} onChange={e => setColMapping({...colMapping, qty_header: e.target.value})}>
              <option value="" disabled>Select column...</option>
              {detectedHeaders.map(h => <option key={h} value={h}>{h}</option>)}
            </Select>
            <Select label="UOM" value={colMapping.uom_header} onChange={e => setColMapping({...colMapping, uom_header: e.target.value})}>
              <option value="">Not present</option>
              {detectedHeaders.map(h => <option key={h} value={h}>{h}</option>)}
            </Select>

            <Select label="Rate" value={colMapping.rate_header} onChange={e => setColMapping({...colMapping, rate_header: e.target.value})}>
              <option value="" disabled>Select column...</option>
              {detectedHeaders.map(h => <option key={h} value={h}>{h}</option>)}
            </Select>
            <Select label="Rate is" value={colMapping.rate_includes_gst ? "true" : "false"} onChange={e => setColMapping({...colMapping, rate_includes_gst: e.target.value === "true"})}>
              <option value="false">Excluding GST</option>
              <option value="true">Including GST</option>
            </Select>

            <Select label="Discount" value={colMapping.discount_header} onChange={e => setColMapping({...colMapping, discount_header: e.target.value})}>
              <option value="">Not present</option>
              {detectedHeaders.map(h => <option key={h} value={h}>{h}</option>)}
            </Select>
            {colMapping.discount_header && (
              <Select label="Discount is" value={colMapping.discount_treatment} onChange={e => setColMapping({...colMapping, discount_treatment: e.target.value})}>
                <option value="already_in_rate">Already deducted in Rate</option>
                <option value="apply_to_rate">Apply Discount to Rate</option>
                <option value="ignore">Ignore Discount</option>
              </Select>
            )}

            <Select label="Amount" value={colMapping.amount_header} onChange={e => setColMapping({...colMapping, amount_header: e.target.value})}>
              <option value="" disabled>Select column...</option>
              {detectedHeaders.map(h => <option key={h} value={h}>{h}</option>)}
            </Select>
            <Select label="Amount is" value={colMapping.amount_includes_gst ? "true" : "false"} onChange={e => setColMapping({...colMapping, amount_includes_gst: e.target.value === "true"})}>
              <option value="false">Excluding GST / Taxable</option>
              <option value="true">Including GST</option>
            </Select>
          </div>

          <div className="flex gap-3 pt-2">
            <Button onClick={handleApplyMapping} className="flex-1 bg-amber-600 hover:bg-amber-700 text-white" disabled={isExtracting}>
              {isExtracting ? "Processing..." : mappingFailed ? "Try Mapping Again" : "Use These Columns"}
            </Button>
            {mappingFailed && (
              <Button onClick={() => setForceManual(true)} variant="secondary" className="flex-1">
                Continue to Manual Review
              </Button>
            )}
          </div>
        </Card>
      ) : (
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
                    <div className="flex justify-between items-start pr-6">
                      <p className="text-base font-bold text-gray-900 dark:text-gray-100 leading-tight pr-4">{item.name}</p>
                      <p className="text-base font-bold text-gray-900 dark:text-gray-100 whitespace-nowrap">₹ {item.amount.toFixed(2)}</p>
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
                        {item.qty} {item.uom} × ₹{item.rate}
                      </p>
                    </div>
                    <ChevronDown className="absolute right-3 top-4 w-5 h-5 text-gray-300" />
                  </>
                ) : (
                  <div className="flex flex-col gap-4">
                    <div className="flex justify-between items-center">
                      <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase">Edit Item</h4>
                      <button onClick={(e) => { e.stopPropagation(); setEditingItemIdx(null); }} className="text-teal-600 text-sm font-bold">Done</button>
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Item Name (from invoice)</label>
                      <input
                        value={item.name}
                        onChange={e => updateItemVal(idx, 'name', e.target.value)}
                        className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500"
                      />
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Tally Item Mapping</label>
                      <MasterAutocomplete
                        placeholder="Select tally item..."
                        rawItemName={item.name || ""}
                        confirmed={meta.stock_items}
                        masterStates={meta.master_states?.stock_items}
                        value={item.mapped_name || ""}
                        onChange={val => handleItemMapChange(idx, val)}
                        onCreate={(val) => handleCreateItem(val, items[editingItemIdx].uom)}
                        isCreating={creatingMaster?.type === 'ITEM'}
                      />
                      {!isKnownItem && <span className="text-xs font-bold text-amber-600 ml-1">Needs Match</span>}
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
                        <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Rate (₹)</label>
                        <input
                          type="number" step="0.01"
                          value={item.rate}
                          onChange={e => updateItemVal(idx, 'rate', e.target.value)}
                          className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:border-teal-500"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Amount (₹)</label>
                        <input
                          type="number" step="0.01"
                          value={item.amount}
                          onChange={e => updateItemVal(idx, 'amount', parseFloat(e.target.value) || 0)}
                          className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-gray-900 dark:text-gray-100 outline-none font-bold"
                        />
                      </div>
                    </div>

                    <Button variant="secondary" onClick={(e) => { e.stopPropagation(); removeItem(idx); }} className="text-red-600 border-red-200 mt-2">
                      <Trash2 className="w-4 h-4 mr-2" /> Remove Item
                    </Button>
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

        <Button variant="secondary" onClick={() => setItems([...items, { name: "New Item", mapped_name: "", qty: 1, rate: 0, amount: 0, uom: "PCS" }])} className="mt-2 text-teal-600">
          <PlusCircle className="w-4 h-4" /> Add empty item
        </Button>
      </div>

      <Card className="flex flex-col gap-3">
        <div className="flex justify-between items-center cursor-pointer" onClick={() => setShowTaxEdit(!showTaxEdit)}>
          <span className="font-bold text-gray-900 dark:text-white">Taxes & Totals</span>
          <Button variant="secondary" className="px-3 py-1 min-h-0 h-8 text-xs">{showTaxEdit ? 'Done' : 'Edit'}</Button>
        </div>

        <div className="flex justify-between text-sm font-medium text-gray-500 mt-2">
          <span>Subtotal</span>
          <span>₹ {itemSubtotal.toFixed(2)}</span>
        </div>

        {showTaxEdit ? (
          <div className="space-y-3 pt-3 border-t border-gray-100 dark:border-gray-800">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">GST Rate (%)</label>
                <input type="number" value={invoice.gst_rate} onChange={e => handleTaxRecalculation(e.target.value, invoice.tax_type)} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none" />
              </div>
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Tax Type</label>
                <select value={invoice.tax_type} onChange={e => handleTaxRecalculation(invoice.gst_rate, e.target.value)} className="w-full p-2 text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none">
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
        ) : (
          <div className="flex flex-col gap-1 text-sm font-medium text-gray-500">
            {invoice.tax_type === 'local' ? (
              <>
                <div className="flex justify-between">
                  <span>CGST {(invoice.gst_rate / 2)}%</span>
                  <span>₹ {(invoice.cgst || 0).toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span>SGST {(invoice.gst_rate / 2)}%</span>
                  <span>₹ {(invoice.sgst || 0).toFixed(2)}</span>
                </div>
              </>
            ) : (
              <div className="flex justify-between">
                <span>IGST {invoice.gst_rate}%</span>
                <span>₹ {(invoice.igst || 0).toFixed(2)}</span>
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

        <div className="h-px bg-gray-100 dark:bg-gray-800 mt-2" />
        <div className="flex justify-between text-xl font-black text-gray-900 dark:text-white">
          <span>Total</span>
          <span>₹ {grandTotal.toFixed(2)}</span>
        </div>
      </Card>

      <Button onClick={handlePost} disabled={isPosting || !invoice.cost_center}>
        {isPosting ? "Sending..." : "Push to Tally"}
      </Button>
        </>
      )}

    </div>
  );
}
