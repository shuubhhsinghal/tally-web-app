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
import { UploadCloud, Image as ImageIcon, AlertCircle, ChevronDown, ChevronRight, PlusCircle, Trash2, Edit3, Camera, History, X, EyeOff, Plus, User, Store, AlertTriangle, Search } from "lucide-react";
import { calculateAndReconcileV4 } from './utils/reconciliation';
import { CameraCapture } from './CameraCapture';
import { MasterAutocomplete } from '@/components/ui/MasterAutocomplete';


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
 const newIdx = adjustment.items.length;
 setAdjustment(prev => ({ ...prev, items: [...prev.items, newRow] }));
 // Fetch a sensible default rate in the background (so the row always has
 // something even if the picker below gets dismissed without a pick), but
 // open the 2-year purchase-history picker immediately too -- picking from
 // real history should be the primary flow, not a secondary icon most
 // people won't notice.
 refreshReturnItemRate(name, 1);
 openHistoryPicker(newIdx, name);
 };

 const handleReturnItemQtyChange = (idx, qty) => {
 setAdjustment(prev => ({
 ...prev,
 items: prev.items.map((i, iidx) => iidx === idx ? { ...i, qty } : i)
 }));
 };

 const handleReturnItemRateChange = (idx, rate) => {
 // Typing a valid rate clears whatever auto-lookup state was showing
 // (loading/error) -- a manual entry always wins over the fetched one.
 setAdjustment(prev => ({
 ...prev,
 items: prev.items.map((i, iidx) => iidx === idx
 ? { ...i, rate, rateStatus: (rate !== '' && Number(rate) > 0) ? 'ok' : i.rateStatus, rateError: null }
 : i)
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

 // An extracted invoice arrives with only the raw gst_rate_metadata/tax_type
 // fields the backend detected (see extraction_engine.py) -- not the
 // gst_rate/cgst/sgst/rounding_off fields this screen actually edits, which
 // stayed undefined until a human manually clicked a GST-rate pill (the
 // only thing that ever computed them, via handleTaxRecalculation). Seed
 // them once real item amounts are available: use whatever rate the
 // invoice actually detected, falling back to 5% (the most common rate)
 // only if extraction found none. This also means rounding_off is always
 // freshly computed from the current subtotal via handleTaxRecalculation's
 // own math -- never blindly trusted from the invoice's own printed figure,
 // which can drift once items are mapped/edited/rate-corrected.
 useEffect(() => {
 const hasRealAmount = items.some(i => (Number(i.amount) || 0) > 0 || (Number(i.final_amount) || 0) > 0);
 if (invoice && invoice.gst_rate === undefined && hasRealAmount) {
 const detectedRate = Number(invoice.gst_rate_metadata) || 5;
 handleTaxRecalculation(detectedRate, invoice.tax_type || 'local');
 }
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [invoice, items]);

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

 const handleTaxRecalculation = (newRate, newTaxType, newGstMethod = null, itemsOverride = null) => {
 const rate = Number(newRate);
 const methodToUse = newGstMethod || (v4Config?.gst_recording_method || "included_in_rate");
 const gstBasis = v4Config?.gst_basis_override || "exclusive";

 let updatedItems = itemsOverride ? [...itemsOverride] : [...items];

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

 // Persist the per-item amount/rate this function already computed above --
 // for a manually-entered invoice (no v4RawData) nothing else will ever
 // save these, so skipping this left final_amount/final_rate permanently
 // undefined and the displayed Total silently short by the GST portion.
 // For an extracted invoice this is still just the "immediate prediction"
 // the comment above describes -- the v4Data effect below recomputes and
 // overwrites it properly moments later, so persisting it here changes
 // nothing for that path except filling the brief gap correctly too.
 setItems(updatedItems);
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
 return { status: "in_tally", text: "✓ In Tally", cls: "text-neutral-700", borderCls: "border-divider focus:ring-neutral-500/50", bgCls: "bg-transparent " };
 }

 // Check masterStates
 if (meta.master_states?.ledgers) {
 const pendingMatch = meta.master_states.ledgers.find(m => norm(m.normalized_name) === targetNorm || norm(m.name) === targetNorm);
 if (pendingMatch) {
 if (pendingMatch.state === "syncing") return { status: "syncing", text: "↻ Syncing", cls: "text-accent-700", borderCls: "border-accent focus:ring-accent/50", bgCls: "bg-accent/8 " };
 if (pendingMatch.state === "pending") return { status: "in_queue", text: "⏳ In Queue", cls: "text-accent-700", borderCls: "border-accent focus:ring-accent/50", bgCls: "bg-accent/8 " };
 if (pendingMatch.state === "failed") return { status: "failed", text: "⚠ Supplier creation failed", cls: "text-accent-800", borderCls: "border-accent focus:ring-accent/50", bgCls: "bg-accent/8 ", error: pendingMatch.error };
 }
 }

 // Not found
 return { status: "not_found", text: "⚠ New supplier — not found in Tally or Queue", cls: "text-accent-800", borderCls: "border-accent focus:ring-accent/50", bgCls: "bg-accent/8 " };
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
 // Stage rather than auto-extract, same as the camera-capture path --
 // lets the "Add Another Page" / "Done & Process Invoice" prompt show
 // instead of parsing before the user's had a chance to add more pages.
 setStagedFiles(prev => [...prev, ...heicStaged]);
 }
 };

 const handleCropQueueCapture = (file, points) => {
 const staged = { file, previewUrl: URL.createObjectURL(file), cropPoints: points };
 croppedBatchRef.current = [...croppedBatchRef.current, staged];

 const remaining = filesToCrop.slice(1);
 setFilesToCrop(remaining);

 if (remaining.length === 0) {
 const finalBatch = [...croppedBatchRef.current];
 setStagedFiles(prev => [...prev, ...finalBatch]);
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

 // In manual mode, item edits must also refresh the invoice-level cgst/
 // sgst/rounding_off -- those are only ever computed inside
 // handleTaxRecalculation, so without this, editing an item's qty/rate
 // *after* picking a GST rate (the natural order of manual entry) left
 // the displayed and posted GST amounts stale at whatever they were
 // before this edit (often 0, since no item had a rate yet when the GST
 // rate button was first clicked). Passing newItems directly avoids the
 // stale-closure issue of handleTaxRecalculation reading its own `items`
 // capture, which wouldn't yet reflect this edit.
 if (forceManual) {
 handleTaxRecalculation(invoice?.gst_rate, invoice?.tax_type, null, newItems);
 } else {
 setItems(newItems);
 }
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
 // The backend's own message already says whether a return was queued
 // alongside the invoice -- showing a hardcoded string here would hide
 // that (a return saved this way gets no other confirmation until it
 // shows up later in the Queue list).
 if (data.message) {
 showToast(data.message);
 } else if (data.reason === 'pending_master_dependency') {
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
 // gst_rate deliberately left unset -- the seeding effect above (keyed off
 // invoice.gst_rate === undefined) fills it in as soon as the first item is
 // added, defaulting to 5% and computing cgst/sgst/rounding_off from real
 // item amounts via handleTaxRecalculation. Hardcoding it here would skip
 // that computation entirely, since the effect only fires while undefined.
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
 {stagedFiles.length === 0 ? (
 <div className="space-y-4">
 <div className="grid grid-cols-2 gap-4">
 <div className="relative h-[160px]">
 <input type="file" multiple accept="image/*,.heic,.heif,image/heic,image/heif" className="hidden" id="file-upload" onChange={handleFileChange} ref={fileInputRef} />
 <div 
 onClick={() => !isExtracting && setShowUploadOptions(true)}
 className={`flex flex-col items-center justify-center p-4 h-full text-center rounded-xl transition-colors ${isExtracting ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'} border border-dashed bg-accent/8 border-accent`}
 >
 {isExtracting ? (
 <div className="w-7 h-7 rounded-full border-2 border-accent border-t-transparent animate-spin mb-4" />
 ) : (
 <UploadCloud className="h-7 w-7 text-accent-700 mb-4 stroke-[1.5]" />
 )}
 <span className="text-[18px] font-heading font-semibold text-text mb-2 leading-tight">
 {isExtracting ? "Reading invoice..." : "Upload or take invoice"}
 </span>
 <span className="text-[12.5px] font-body text-neutral-600 leading-tight">
 Camera or gallery · add every page
 </span>
 </div>
 </div>
 
 <div 
 onClick={initializeManualDraft}
 className={`flex flex-col items-center justify-center p-4 h-[160px] text-center rounded-xl transition-colors border border-dashed border-divider bg-transparent ${isExtracting ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
 >
 <Edit3 className="h-7 w-7 text-neutral-700 mb-4 stroke-[1.5]" />
 <span className="text-[18px] font-heading font-semibold text-text mb-2 leading-tight">
 Enter purchase manually
 </span>
 <span className="text-[12.5px] font-body text-neutral-600 leading-tight">
 Type items, quantities and rates
 </span>
 </div>
 </div>

 {!isExtracting && (
 <div className="flex items-start gap-2.5 text-neutral-600 text-[13.5px] mt-2 px-1">
 <EyeOff className="w-[18px] h-[18px] text-accent-700 shrink-0 stroke-[1.5] mt-[-1px]" />
 <span className="leading-tight font-body">Offline: photos stay on this phone and are read once you reconnect.</span>
 </div>
 )}
 </div>
 ) : (
 <div className="flex flex-col h-full">
 <div className="space-y-1 mb-4 mt-2">
 <h4 className="text-[11px] font-bold text-accent-700 uppercase tracking-widest">Invoice Photos</h4>
 <h2 className="text-[26px] font-heading font-semibold text-text leading-tight">{stagedFiles.length} page{stagedFiles.length > 1 ? 's' : ''} added</h2>
 <p className="text-neutral-600 text-[14.5px] font-body leading-relaxed pt-1">Add every page of the bill. Remove any blurry shot and retake it.</p>
 </div>

 <div className="flex gap-4 overflow-x-auto pb-8 pt-2">
 {stagedFiles.map((sf, idx) => (
 <div key={idx} className="relative flex-shrink-0 w-[140px] h-[190px] rounded-lg overflow-hidden group shadow-sm bg-surface">
 {sf.file.type.includes("pdf") ? (
 <div className="w-full h-full flex items-center justify-center text-xs font-bold text-neutral-600">PDF</div>
 ) : (
 <img src={sf.previewUrl} alt={`Page ${idx + 1}`} className="w-full h-full object-cover" />
 )}
 <button 
 onClick={() => removeStagedFile(idx)} 
 className="absolute top-2 right-2 bg-bg/90 text-text rounded-full w-[26px] h-[26px] flex items-center justify-center shadow-sm hover:bg-bg transition-colors z-10"
 >
 <X className="w-4 h-4 stroke-[2]" />
 </button>
 <div className="absolute bottom-2 left-2 bg-bg/90 text-text text-[13px] px-2 py-0.5 rounded shadow-sm font-semibold z-10">
 {idx + 1}
 </div>
 </div>
 ))}

 <div 
 onClick={() => !isExtracting && setShowUploadOptions(true)}
 className="flex-shrink-0 w-[140px] h-[190px] border border-dashed border-accent rounded-lg flex flex-col items-center justify-center text-center p-4 cursor-pointer hover:bg-accent/8 transition-colors"
 >
 <Plus className="w-6 h-6 text-accent-700 mb-2 stroke-[1.5]" />
 <span className="text-accent-700 font-heading font-semibold text-[16px] leading-tight">Add more photos</span>
 </div>
 </div>

 <div className="mt-8 pt-4 border-t border-divider space-y-4">
 <div className="flex items-start gap-2.5 text-neutral-600 text-[13.5px] px-1 pb-1">
 <EyeOff className="w-[18px] h-[18px] text-accent-700 shrink-0 stroke-[1.5] mt-[-1px]" />
 <span className="leading-tight font-body">Offline: the photos will be saved and read when you reconnect.</span>
 </div>
 
 <button 
 onClick={handleExtract} 
 disabled={isExtracting}
 className="w-full py-4 bg-accent/8 border border-accent text-accent-700 font-heading font-semibold text-[18px] rounded-lg transition-colors flex items-center justify-center hover:bg-accent/14"
 >
 {isExtracting ? (
 <div className="w-5 h-5 rounded-full border-2 border-accent border-t-transparent animate-spin mr-3" />
 ) : null}
 {isExtracting ? "Processing..." : "Save photos · read when online"}
 </button>
 <button 
 onClick={() => setStagedFiles([])}
 className="w-full py-4 bg-transparent border border-divider text-text font-heading font-semibold text-[18px] rounded-lg transition-colors hover:bg-surface"
 >
 Start over
 </button>
 </div>
 </div>
 )}

 {showUploadOptions && (
 <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 transition-opacity">
 <div className="absolute inset-0" onClick={() => setShowUploadOptions(false)}></div>
 <div className="relative bg-surface rounded-t-3xl sm:rounded-2xl w-full max-w-md p-6 pb-8 flex flex-col gap-4 shadow-xl animate-in slide-in-from-bottom-full sm:slide-in-from-bottom-0 sm:fade-in-0">
 <div className="w-12 h-1.5 bg-divider rounded-full mx-auto mb-2 sm:hidden"></div>
 <h3 className="text-[22px] font-heading font-semibold text-text mb-2">Add invoice photo</h3>
 
 <button 
 className="flex items-center text-left p-4 bg-bg border border-divider rounded-lg hover:bg-surface dark:hover:bg-surface transition-colors w-full"
 onClick={() => { 
 setShowUploadOptions(false); 
 if (isAndroid && nativeCameraInputRef.current) {
 nativeCameraInputRef.current.click();
 } else {
 setShowCamera(true); 
 }
 }}
 >
 <Camera className="w-6 h-6 mr-4 text-accent-700 stroke-[1.5]" /> 
 <div className="flex flex-col">
 <span className="text-[17px] font-heading font-semibold text-text leading-tight">Take photo</span>
 <span className="text-[13px] font-body text-neutral-600 mt-0.5 leading-tight">Use the camera</span>
 </div>
 </button>

 <button 
 className="flex items-center text-left p-4 bg-bg border border-accent dark:border-accent rounded-lg hover:bg-accent/8 dark:hover:bg-surface transition-colors w-full"
 onClick={() => { setShowUploadOptions(false); fileInputRef.current.click(); }}
 >
 <ImageIcon className="w-6 h-6 mr-4 text-accent-700 stroke-[1.5]" /> 
 <div className="flex flex-col">
 <span className="text-[17px] font-heading font-semibold text-text leading-tight">Upload from gallery</span>
 <span className="text-[13px] font-body text-neutral-600 mt-0.5 leading-tight">Pick one or more images</span>
 </div>
 </button>

 <button 
 className="mt-4 text-[15px] font-heading font-medium text-accent-700 underline underline-offset-4 hover:text-accent-800 transition-colors text-center w-full" 
 onClick={() => {
 setShowUploadOptions(false);
 // Additional logic if there is a sample page feature
 }}
 >
 No bill handy? Use a sample page
 </button>
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
 <Card className="flex flex-col gap-6 !border-accent/50 dark:!border-accent/30 bg-gradient-to-br from-teal-50/50 to-white dark:from-teal-900/10 dark:to-bg shadow-sm relative overflow-hidden">
 <div className="absolute top-0 right-0 p-4 opacity-10">
 <AlertCircle className="w-24 h-24 text-accent-700" />
 </div>

 <div className="relative z-10 flex flex-col gap-1 text-accent-800 ">
 <h2 className="text-xl font-bold tracking-tight">Invoice Configuration</h2>
 <p className="text-sm opacity-80">Confirm the invoice layout so we can calculate GST correctly.</p>
 </div>

 <div className="relative z-10 flex flex-col gap-6 divide-y divide-divider ">

 {/* Question 1: Amount Column Selection */}
 <div className="flex flex-col gap-3 pt-6 first:pt-0">
 <label className="text-sm font-bold text-text flex flex-col">
 <span className="flex items-center gap-1.5"><span className="w-5 h-5 rounded-full bg-accent/15 text-accent-700 flex items-center justify-center text-xs">1</span> Amount column</span>
 </label>
 <div className="relative group max-w-md">
 <select
 className="w-full appearance-none bg-bg border-2 border-divider text-text text-sm rounded-xl px-4 py-3 pr-10 outline-none focus:border-accent focus:ring-4 focus:ring-accent/20 transition-all font-medium cursor-pointer"
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
 <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-neutral-500 pointer-events-none group-hover:text-accent-700 transition-colors" />
 </div>
 </div>

 {/* Question 2: GST Basis */}
 <div className="flex flex-col gap-3 pt-6">
 <label className="text-sm font-bold text-text flex flex-col">
 <span className="flex items-center gap-1.5"><span className="w-5 h-5 rounded-full bg-accent/15 text-accent-700 flex items-center justify-center text-xs">2</span> Does this amount include GST?</span>
 </label>
 <div className="flex flex-col gap-2 max-w-md">
 <label className={`flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${v4Data.calculated_data?.gst_basis === "inclusive" ? 'border-accent bg-accent/5 ' : 'border-divider bg-bg hover:border-divider '}`}>
 <input type="radio" name="basis" value="inclusive" className="text-accent-700 focus:ring-accent w-4 h-4" checked={v4Data.calculated_data?.gst_basis === "inclusive"} onChange={e => handleV4ConfigChange({ gst_basis_override: e.target.value })} />
 <span className="text-sm font-medium text-text ">Yes, GST included</span>
 </label>
 <label className={`flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${v4Data.calculated_data?.gst_basis === "exclusive" ? 'border-accent bg-accent/5 ' : 'border-divider bg-bg hover:border-divider '}`}>
 <input type="radio" name="basis" value="exclusive" className="text-accent-700 focus:ring-accent w-4 h-4" checked={v4Data.calculated_data?.gst_basis === "exclusive"} onChange={e => handleV4ConfigChange({ gst_basis_override: e.target.value })} />
 <span className="text-sm font-medium text-text ">No, GST excluded</span>
 </label>
 </div>
 </div>

 {/* Question 3: Accounting Treatment */}
 <div className="flex flex-col gap-3 pt-6">
 <label className="text-sm font-bold text-text flex flex-col">
 <span className="flex items-center gap-1.5"><span className="w-5 h-5 rounded-full bg-accent/15 text-accent-700 flex items-center justify-center text-xs">3</span> Purchase rate treatment</span>
 </label>
 <div className="flex flex-col gap-2 max-w-md">
 <label className={`flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${v4Data.calculated_data?.gst_treatment === "included_in_rate" ? 'border-accent bg-accent/5 ' : 'border-divider bg-bg hover:border-divider '}`}>
 <input type="radio" name="treatment" value="included_in_rate" className="text-accent-700 focus:ring-accent w-4 h-4" checked={v4Data.calculated_data?.gst_treatment === "included_in_rate"} onChange={e => handleV4ConfigChange({ gst_recording_method: e.target.value })} />
 <span className="text-sm font-medium text-text ">Include GST in purchase rate</span>
 </label>
 <label className={`flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${v4Data.calculated_data?.gst_treatment === "separate_ledger" ? 'border-accent bg-accent/5 ' : 'border-divider bg-bg hover:border-divider '}`}>
 <input type="radio" name="treatment" value="separate_ledger" className="text-accent-700 focus:ring-accent w-4 h-4" checked={v4Data.calculated_data?.gst_treatment === "separate_ledger"} onChange={e => handleV4ConfigChange({ gst_recording_method: e.target.value })} />
 <span className="text-sm font-medium text-text ">Record GST separately</span>
 </label>
 </div>
 </div>

 </div>
 </Card>
 )}

 <div className="flex flex-col gap-4 p-4 bg-surface border border-divider rounded-lg">
 <div className="grid grid-cols-2 gap-3">
 <div className="space-y-1.5">
 <label className="text-[13px] font-body text-neutral-600 font-medium">Invoice date</label>
 <input
 type="date"
 value={invoice.date || ""}
 onChange={e => setInvoice({ ...invoice, date: e.target.value })}
 className="w-full p-2.5 text-[15px] font-body text-text bg-surface border border-divider rounded outline-none focus:border-accent transition-colors"
 />
 </div>
 <div className="space-y-1.5">
 <label className="text-[13px] font-body text-neutral-600 font-medium">Invoice no.</label>
 <input
 type="text"
 placeholder="e.g. SH/2245"
 value={invoice.invoice_number || ""}
 onChange={e => setInvoice({ ...invoice, invoice_number: e.target.value })}
 className="w-full p-2.5 text-[15px] font-body text-text bg-surface border border-divider rounded outline-none focus:border-accent transition-colors placeholder:text-neutral-500"
 />
 </div>
 </div>

 {(() => {
 const supplierStatus = getSupplierStatus(invoice.supplier);
 return (
 <div className="space-y-1.5">
 <label className="text-[13px] font-body text-neutral-600 font-medium">Supplier</label>
 <div className="relative">
 <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-[18px] h-[18px] text-neutral-500 z-10 pointer-events-none" />
 <MasterAutocomplete
 value={invoice.supplier || ""}
 onChange={val => setInvoice({ ...invoice, supplier: val })}
 placeholder="Select or type supplier"
 confirmed={meta.suppliers}
 masterStates={meta.master_states?.ledgers}
 onCreate={handleCreateSupplier}
 inputClassName={`!pl-10 !bg-transparent !border-divider !rounded !text-[15px] font-body focus:!border-accent ${supplierStatus ? `${supplierStatus.borderCls} ${supplierStatus.bgCls} text-text` : ""}`}
 createLabel="supplier"
 isCreating={creatingMaster?.type === 'LEDGER'}
 />
 <ChevronDown className="absolute right-3.5 top-1/2 -translate-y-1/2 w-[18px] h-[18px] text-neutral-500 pointer-events-none" />
 </div>
 {supplierStatus && (
 <div className={`text-xs font-bold ml-1 flex flex-col ${supplierStatus.cls}`}>
 <span>{supplierStatus.text}</span>
 {supplierStatus.error && <span className="font-normal opacity-80 mt-0.5">{supplierStatus.error}</span>}
 </div>
 )}
 </div>
 );
 })()}

 <div className="space-y-1.5">
 <label className="text-[13px] font-body text-neutral-600 font-medium">Store</label>
 <div className="relative">
 <Store className="absolute left-3.5 top-1/2 -translate-y-1/2 w-[18px] h-[18px] text-neutral-500 z-10 pointer-events-none" />
 <select
 value={invoice?.cost_center || ""}
 onChange={e => setInvoice({ ...invoice, cost_center: e.target.value })}
 disabled={!!lockedStore}
 className="w-full pl-10 pr-10 py-2.5 text-[15px] font-body text-text bg-surface border border-divider rounded outline-none focus:border-accent transition-colors appearance-none"
 >
 <option value="" disabled>Select store</option>
 {meta.stores.map(s => <option key={s} value={s}>{s}</option>)}
 </select>
 <ChevronDown className="absolute right-3.5 top-1/2 -translate-y-1/2 w-[18px] h-[18px] text-neutral-500 pointer-events-none" />
 </div>
 </div>

 {forceManual && (
 <div className="flex items-center justify-between p-3.5 bg-bg border border-divider rounded mt-1 shadow-sm">
 <div className="flex flex-col">
 <span className="text-[15px] font-heading font-semibold text-text">GST inclusive rate</span>
 <span className="text-[12.5px] font-body text-neutral-600">Item rates already include GST</span>
 </div>
 <label className="relative inline-flex items-center cursor-pointer">
 <input 
 type="checkbox" 
 className="sr-only peer" 
 checked={v4Config?.gst_recording_method !== 'exclude_gst'} 
 onChange={e => handleTaxRecalculation(invoice?.gst_rate, invoice.tax_type, e.target.checked ? "included_in_rate" : "exclude_gst")} 
 />
 <div className="w-11 h-6 bg-divider peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-bg after:border-divider after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-accent"></div>
 </label>
 </div>
 )}

 {previewUrl && (
 <a href={previewUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 text-accent-700 font-medium text-sm pt-2 border-t border-divider ">
 <ImageIcon className="w-4 h-4" />
 View original photo
 </a>
 )}
 </div>

 <>
 <div className="space-y-3">
 <div className="flex justify-between items-center px-1 mb-2 mt-4">
 <h3 className="text-[11px] font-bold text-neutral-600 uppercase tracking-widest">{items.length} ITEM{items.length !== 1 ? 'S' : ''}</h3>
 <span className="text-[13px] font-bold text-neutral-700">₹{itemSubtotal.toFixed(2)}</span>
 </div>

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
 <div
 key={idx}
 onClick={() => !isEditing && setEditingItemIdx(idx)}
 className={`flex flex-col gap-2 relative bg-bg border border-divider rounded p-4 ${isEditing ? 'ring-1 ring-accent shadow-sm' : 'cursor-pointer hover:bg-surface dark:hover:bg-surface shadow-[0_1px_2px_rgba(0,0,0,0.02)]'}`}
 >
 {!isEditing ? (
 <>
 <div className="flex justify-between items-start">
 <p className="text-[18px] font-heading font-semibold text-text leading-tight pr-4">{item.name}</p>
 <div className="flex items-center gap-1 -mt-1 -mr-2">
 <p className="text-[15px] font-bold text-text whitespace-nowrap mr-2">
 ₹{isGstIncluded && item.final_amount !== undefined ? Number(item.final_amount || 0).toFixed(2) : Number(item.amount || 0).toFixed(2)}
 </p>
 <ChevronDown className="w-5 h-5 text-neutral-500" />
 <button
 type="button"
 onClick={(e) => { e.stopPropagation(); removeItem(idx); }}
 className="text-neutral-500 hover:text-accent-800 p-2 rounded-full transition-colors focus:outline-none"
 aria-label="Remove Item"
 >
 <Trash2 className="w-[18px] h-[18px]" />
 </button>
 </div>
 </div>

 <div className="flex justify-between items-end mt-3">
 <div className="flex items-center gap-1.5">
 {itemStateObj ? (
 itemStateObj.state === 'failed' ? (
 <span className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-accent-800 bg-accent/8 px-2 py-1 rounded cursor-pointer border border-accent" onClick={(e) => { e.stopPropagation(); alert(itemStateObj.error || "Item failed to sync."); }}>
 <AlertTriangle className="w-[14px] h-[14px]" /> Failed
 </span>
 ) : itemStateObj.state === 'pending' ? (
 <span className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 px-2 py-1 rounded border border-accent">
 ⏳ In Queue
 </span>
 ) : itemStateObj.state === 'syncing' ? (
 <span className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 px-2 py-1 rounded border border-accent">
 ↻ Syncing
 </span>
 ) : (
 <span className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-neutral-700 bg-transparent px-2 py-1 rounded border border-divider">
 ✓ Matched
 </span>
 )
 ) : isKnownItem ? (
 <span className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-neutral-700 bg-transparent px-2 py-1 rounded border border-divider">
 ✓ Matched
 </span>
 ) : (
 <span className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-accent-700 bg-transparent px-2 py-1 rounded border border-accent">
 <AlertTriangle className="w-[14px] h-[14px]" /> Needs Match
 </span>
 )}
 </div>
 <p className="text-[13px] font-body text-neutral-600">
 {item.qty} {item.uom} × ₹{isGstIncluded && item.final_rate !== undefined ? Number(item.final_rate || 0).toFixed(2) : Number(item.rate || 0).toFixed(2)}
 </p>
 </div>

 </>
 ) : (
 <div className="flex flex-col gap-4">
 <div className="flex justify-between items-center">
 <h4 className="text-[13px] font-bold text-text uppercase tracking-wide">Edit Item</h4>
 <div className="flex items-center gap-4">
 <button
 type="button"
 onClick={(e) => { e.stopPropagation(); removeItem(idx); }}
 className="text-neutral-500 hover:text-accent-800 p-2 -my-2 rounded-full transition-colors focus:outline-none"
 aria-label="Remove Item"
 >
 <Trash2 className="w-5 h-5" />
 </button>
 <button type="button" onClick={(e) => { e.stopPropagation(); setEditingItemIdx(null); }} className="text-accent-700 text-[15px] font-bold p-2 -my-2 -mr-2 focus:outline-none">Done</button>
 </div>
 </div>

 {!forceManual && (
 <div className="space-y-1.5">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">Item Name (from invoice)</label>
 <textarea
 value={item.name}
 onChange={e => updateItemVal(idx, 'name', e.target.value)}
 rows={2}
 className="w-full p-2 text-[15px] rounded border border-divider bg-bg text-text outline-none focus:border-accent resize-none"
 />
 </div>
 )}

 <div className="space-y-1.5">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">
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
 inputClassName="!border-divider focus:!border-accent !text-[15px]"
 />
 {!isKnownItem && !forceManual && <span className="text-xs font-bold text-accent-700 ml-1">Needs Match</span>}
 </div>

 <div className="grid grid-cols-2 gap-3">
 <div className="space-y-1.5">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">Quantity</label>
 <input
 type="number" step="0.01"
 value={item.qty}
 onChange={e => updateItemVal(idx, 'qty', e.target.value)}
 className="w-full p-2 text-[15px] rounded border border-divider bg-bg text-text outline-none focus:border-accent"
 />
 </div>
 <div className="space-y-1.5">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">UOM</label>
 <input
 list="uoms-list"
 value={item.uom || ""}
 onChange={e => updateItemVal(idx, 'uom', e.target.value.toUpperCase())}
 className="w-full p-2 text-[15px] rounded border border-divider bg-bg text-text outline-none focus:border-accent uppercase"
 />
 </div>
 <div className="space-y-1.5">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">
 {isGstIncluded && forceManual ? "Rate (Incl. GST) (₹)" : isGstIncluded ? "Rate (Excl. GST) (₹)" : "Rate (₹)"}
 </label>
 <input
 type="number" step="0.01"
 value={isGstIncluded && forceManual ? item.final_rate : item.rate}
 onChange={e => updateItemVal(idx, isGstIncluded && forceManual ? 'final_rate' : 'rate', e.target.value)}
 className="w-full p-2 text-[15px] rounded border border-divider bg-bg text-text outline-none focus:border-accent"
 />
 </div>
 <div className="space-y-1.5">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">
 {isGstIncluded && forceManual ? "Amount (Incl. GST) (₹)" : isGstIncluded ? "Amount (Excl. GST) (₹)" : "Amount (₹)"}
 </label>
 <input
 type="number" step="0.01"
 value={isGstIncluded && forceManual ? item.final_amount : item.amount}
 onChange={e => updateItemVal(idx, isGstIncluded && forceManual ? 'final_amount' : 'amount', parseFloat(e.target.value) || 0)}
 className="w-full p-2 text-[15px] rounded border border-divider bg-surface text-text outline-none font-bold"
 />
 </div>
 </div>


 </div>
 )}
 </div>
 );
 })}
 </div>

 <datalist id="uoms-list">
 {meta.uoms.map(u => <option key={u} value={u} />)}
 <option value="PCS" /><option value="NOS" /><option value="KGS" /><option value="BOX" /><option value="PKT" />
 </datalist>

 <button 
 onClick={() => {
 setItems([...items, { name: "", mapped_name: "", qty: 1, rate: 0, amount: 0, uom: "PCS" }]);
 setEditingItemIdx(items.length);
 }} 
 className="w-full py-4 mt-2 border border-dashed border-accent bg-transparent text-accent-700 font-heading font-semibold text-[17px] rounded-lg flex items-center justify-center gap-2 hover:bg-accent/8 transition-colors"
 >
 <Plus className="w-5 h-5 stroke-[2]" /> Add item
 </button>
 </div>

 <div className="flex flex-col gap-4 p-4 bg-surface border border-divider rounded-lg">
 <div className="flex justify-between items-center mb-1">
 <span className="text-[20px] font-heading font-semibold text-text">Taxes & totals</span>
 </div>

 <div className="space-y-4">
 <div className="space-y-2">
 <label className="text-[13px] font-body text-neutral-600 font-medium">GST rate</label>
 <div className="flex border border-divider rounded w-full bg-surface">
 {['0', '5', '18', '40'].map(rate => (
 <button
 key={rate} type="button"
 onClick={() => handleTaxRecalculation(rate, invoice.tax_type)}
 className={`flex-1 py-2.5 text-[15px] font-heading font-semibold transition-colors ${
 invoice.gst_rate == rate ? "text-accent-700 bg-accent/8 border border-accent shadow-sm z-10 -m-[1px] rounded-[1px]" : "text-neutral-700 bg-transparent"
 }`}
 >
 {rate}%
 </button>
 ))}
 </div>
 </div>

 <div className="space-y-2">
 <label className="text-[13px] font-body text-neutral-600 font-medium">Tax type</label>
 <div className="flex border border-divider rounded w-full bg-surface">
 <button
 type="button"
 onClick={() => handleTaxRecalculation(invoice?.gst_rate, 'local')}
 className={`flex-1 py-2.5 text-[15px] font-heading font-semibold transition-colors ${
 invoice.tax_type === 'local' ? "text-accent-700 bg-accent/8 border border-accent shadow-sm z-10 -m-[1px] rounded-[1px]" : "text-neutral-700 bg-transparent"
 }`}
 >
 Local · CGST+SGST
 </button>
 <button
 type="button"
 onClick={() => handleTaxRecalculation(invoice?.gst_rate, 'interstate')}
 className={`flex-1 py-2.5 text-[15px] font-heading font-semibold transition-colors ${
 invoice.tax_type === 'interstate' ? "text-accent-700 bg-accent/8 border border-accent shadow-sm z-10 -m-[1px] rounded-[1px]" : "text-neutral-700 bg-transparent"
 }`}
 >
 Interstate · IGST
 </button>
 </div>
 </div>

 <div className="grid grid-cols-2 gap-3">
 {invoice.tax_type === 'local' ? (
 <>
 <div className="space-y-1.5 flex flex-col p-3 bg-surface border border-divider rounded">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">CGST {(invoice?.gst_rate / 2) || 0}%</label>
 <input type="number" step="0.01" value={invoice.cgst} onChange={e => setInvoice({ ...invoice, cgst: parseFloat(e.target.value) || 0 })} className="w-full text-[15px] font-bold text-text bg-transparent outline-none" />
 </div>
 <div className="space-y-1.5 flex flex-col p-3 bg-surface border border-divider rounded">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">SGST {(invoice?.gst_rate / 2) || 0}%</label>
 <input type="number" step="0.01" value={invoice.sgst} onChange={e => setInvoice({ ...invoice, sgst: parseFloat(e.target.value) || 0 })} className="w-full text-[15px] font-bold text-text bg-transparent outline-none" />
 </div>
 </>
 ) : (
 <div className="space-y-1.5 col-span-2 flex flex-col p-3 bg-surface border border-divider rounded">
 <label className="text-[11px] font-bold text-neutral-600 uppercase tracking-wider">IGST {invoice?.gst_rate || 0}%</label>
 <input type="number" step="0.01" value={invoice.igst} onChange={e => setInvoice({ ...invoice, igst: parseFloat(e.target.value) || 0 })} className="w-full text-[15px] font-bold text-text bg-transparent outline-none" />
 </div>
 )}
 </div>
 
 <div className="space-y-1.5">
 <label className="text-[13px] font-body text-neutral-600 font-medium">Rounding</label>
 <div className="flex gap-2">
 <input type="number" step="0.01" value={invoice.rounding_off} onChange={e => setInvoice({ ...invoice, rounding_off: parseFloat(e.target.value) || 0 })} className="flex-1 p-2.5 text-[15px] font-body text-text bg-surface border border-divider rounded outline-none focus:border-accent transition-colors" />
 <button
 type="button"
 onClick={() => handleTaxRecalculation(invoice?.gst_rate, invoice?.tax_type)}
 title="Recompute rounding from the current items and GST"
 className="px-4 py-2.5 bg-surface border border-divider text-text font-heading font-semibold rounded hover:bg-accent/8 hover:border-accent hover:text-accent-700 transition-colors"
 >
 Round off
 </button>
 </div>
 </div>
 </div>

 <div className="border-t border-divider mt-4 pt-4">
 {isGstIncluded ? (
 <div className="flex flex-col gap-1.5 text-[15px] font-body text-neutral-600 mb-4">
 <div className="flex justify-between">
 <span>Subtotal / taxable value</span>
 <span className="font-medium text-text">₹{itemSubtotal.toFixed(2)}</span>
 </div>
 <div className="flex justify-between text-accent-700 font-semibold">
 <span>GST included in rates</span>
 <span>₹{(computedCgst + computedSgst + computedIgst).toFixed(2)}</span>
 </div>
 {invoice.rounding_off !== 0 && (
 <div className="flex justify-between">
 <span>Round Off</span>
 <span className="font-medium text-text">{invoice.rounding_off > 0 ? '+' : '-'}₹{Math.abs(invoice.rounding_off).toFixed(2)}</span>
 </div>
 )}
 </div>
 ) : (
 <div className="flex flex-col gap-1.5 text-[15px] font-body text-neutral-600 mb-4">
 <div className="flex justify-between">
 <span>Subtotal</span>
 <span className="font-medium text-text">₹{itemSubtotal.toFixed(2)}</span>
 </div>
 {invoice.tax_type === 'local' ? (
 <>
 <div className="flex justify-between">
 <span>CGST {(invoice?.gst_rate / 2) || 0}%</span>
 <span className="font-medium text-text">₹{computedCgst.toFixed(2)}</span>
 </div>
 <div className="flex justify-between">
 <span>SGST {(invoice?.gst_rate / 2) || 0}%</span>
 <span className="font-medium text-text">₹{computedSgst.toFixed(2)}</span>
 </div>
 </>
 ) : (
 <div className="flex justify-between">
 <span>IGST {invoice?.gst_rate || 0}%</span>
 <span className="font-medium text-text">₹{computedIgst.toFixed(2)}</span>
 </div>
 )}
 {invoice.rounding_off !== 0 && (
 <div className="flex justify-between">
 <span>Round Off</span>
 <span className="font-medium text-text">{invoice.rounding_off > 0 ? '+' : '-'}₹{Math.abs(invoice.rounding_off).toFixed(2)}</span>
 </div>
 )}
 </div>
 )}
 
 <div className="flex justify-between items-center">
 <span className="text-[20px] font-heading font-bold text-text">Total</span>
 <span className="text-[24px] font-heading font-bold text-text">₹{grandTotal.toFixed(2)}</span>
 </div>
 </div>
 </div>

 <Card className="flex flex-col gap-3">
 <div className="flex justify-between items-center cursor-pointer" onClick={() => setAdjustment(prev => ({ ...prev, enabled: !prev.enabled }))}>
 <div className="flex items-center gap-2">
 <input type="checkbox" checked={adjustment.enabled} onChange={() => { }} className="w-4 h-4 text-accent-700 focus:ring-accent rounded cursor-pointer" />
 <span className="font-bold text-text">Supplier Adjustment / Return</span>
 </div>
 </div>

 {adjustment.enabled && (
 <div className="space-y-3 pt-3 border-t border-divider ">
 <p className="text-xs text-neutral-600 mb-2">Select the items being returned to this supplier. This will post a Debit Note (with stock effect) to Tally, using each item&apos;s latest purchase rate.</p>

 <div className="space-y-1.5">
 <label className="text-[10px] font-bold text-neutral-600 uppercase tracking-wider">Add Return Item</label>
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
 <div key={ri.name} className="flex flex-col gap-2 bg-surface p-2 rounded-lg border border-divider ">
 <div className="flex items-center gap-2">
 <span className="flex-1 text-sm font-medium text-text break-words" title={ri.name}>{ri.name}</span>
 <button type="button" onClick={() => openHistoryPicker(idx, ri.name)} title="Pick a different rate from purchase history" className="text-accent-700 hover:text-accent-700 shrink-0">
 <History className="w-4 h-4" />
 </button>
 <button type="button" onClick={() => handleRemoveReturnItem(idx)} className="text-accent-700 hover:text-accent-800 shrink-0">
 <Trash2 className="w-4 h-4" />
 </button>
 </div>
 <div className="flex items-center gap-2">
 <input
 type="number" step="0.01" min="0" value={ri.qty}
 onChange={e => handleReturnItemQtyChange(idx, e.target.value)}
 className="w-16 p-2 text-sm rounded border border-divider bg-bg outline-none focus:border-accent"
 />
 <span className="text-[10px] text-neutral-500 w-8">{ri.uom}</span>
 <div className="flex-1 flex items-center justify-end gap-1">
 <span className="text-neutral-500 text-xs">₹</span>
 <input
 type="number" step="0.01" min="0"
 value={ri.rate || ''}
 placeholder={ri.rateStatus === 'loading' ? 'Fetching...' : '0.00'}
 onChange={e => handleReturnItemRateChange(idx, e.target.value)}
 className={`w-20 p-1.5 text-sm text-right rounded border bg-bg outline-none focus:border-accent ${ri.rateStatus === 'error' ? 'border-accent-700' : 'border-divider'}`}
 />
 <span className="text-[10px] text-neutral-500">/unit</span>
 </div>
 <span className="w-20 text-sm text-right font-bold text-text ">
 ₹{((Number(ri.qty) || 0) * (Number(ri.rate) || 0)).toFixed(2)}
 </span>
 </div>
 {ri.rateStatus === 'error' && (
 <p className="text-[11px] text-accent-800 font-medium text-right">No rate found — enter one manually</p>
 )}
 </div>
 ))}
 </div>
 )}

 <div className="grid grid-cols-2 gap-3">
 <div className="space-y-1.5">
 <label className="text-[10px] font-bold text-neutral-600 uppercase tracking-wider">Return Cost Centre <span className="text-accent-800">*</span></label>
 <select value={adjustment.store} onChange={e => setAdjustment({ ...adjustment, store: e.target.value })} disabled={!!lockedStore} className={`w-full p-2 text-sm rounded border bg-bg outline-none focus:border-accent ${!adjustment.store && adjustment.items.length > 0 ? 'border-accent' : 'border-divider '}`}>
 <option value="" disabled>Select Store...</option>
 {meta.stores.map(s => <option key={s} value={s}>{s}</option>)}
 </select>
 </div>
 <div className="space-y-1.5">
 <label className="text-[10px] font-bold text-neutral-600 uppercase tracking-wider">Reason (Optional)</label>
 <input type="text" value={adjustment.reason} onChange={e => setAdjustment({ ...adjustment, reason: e.target.value })} className="w-full p-2 text-sm rounded border border-divider bg-bg outline-none focus:border-accent" placeholder="e.g. Damaged goods" />
 </div>
 </div>
 <div className="space-y-1.5">
 <label className="text-[10px] font-bold text-neutral-600 uppercase tracking-wider">Notes (Optional)</label>
 <input type="text" value={adjustment.notes} onChange={e => setAdjustment({ ...adjustment, notes: e.target.value })} className="w-full p-2 text-sm rounded border border-divider bg-bg outline-none focus:border-accent" />
 </div>
 {returnTotal > 0 && (
 <div className="flex justify-between items-center bg-surface p-3 rounded-lg mt-2 border border-divider ">
 <span className="font-bold text-text text-sm">Net Supplier Payable:</span>
 <span className="font-black text-accent-700 text-lg">₹ {Math.max(0, grandTotal - returnTotal).toFixed(2)}</span>
 </div>
 )}
 </div>
 )}
 </Card>

 {v4Data?.calculated_data?.gst_basis === "unknown" ? (
 <div className="bg-accent/8 border border-accent p-4 rounded-xl text-accent-700 text-sm font-medium">
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
 <>
 <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={closeHistoryPicker} />
 <div className="fixed inset-x-0 bottom-0 sm:inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 pointer-events-none">
 <div
 className="bg-surface border-t sm:border border-divider rounded-t-2xl sm:rounded-lg shadow-lg w-full sm:max-w-lg max-h-[80vh] flex flex-col pointer-events-auto animate-in slide-in-from-bottom-full sm:zoom-in-95"
 onClick={e => e.stopPropagation()}
 >
 <div className="w-12 h-1.5 bg-divider rounded-full mx-auto mt-3 sm:hidden" />
 <div className="flex items-center justify-between px-6 py-4 border-b border-divider">
 <div>
 <h3 className="font-heading font-semibold text-xl">Purchase history</h3>
 <p className="text-sm text-neutral-700 truncate max-w-[240px]">{historyPicker.itemName}</p>
 </div>
 <button onClick={closeHistoryPicker} className="text-neutral-500 hover:text-text transition-colors">
 <X className="w-5 h-5" />
 </button>
 </div>

 <div className="overflow-y-auto flex-1 p-4">
 {historyPicker.loading && (
 <p className="text-sm text-neutral-600 text-center py-8">Fetching purchase history...</p>
 )}
 {historyPicker.error && (
 <div className="text-center py-8">
 <p className="text-sm text-accent-800 mb-2">{historyPicker.error}</p>
 <button onClick={() => openHistoryPicker(historyPicker.itemIdx, historyPicker.itemName)} className="text-sm text-accent-700 font-semibold">Retry</button>
 </div>
 )}
 {!historyPicker.loading && !historyPicker.error && (
 <>
 {historyPicker.source === 'local_cache' && (
 <p className="text-xs border border-accent bg-accent/8 text-accent-700 rounded-md p-2 mb-3">
 Showing last known rates — Tally is offline right now.
 </p>
 )}
 {historyPicker.entries.length === 0 ? (
 <p className="text-sm text-neutral-600 text-center py-8">No purchase history in the last 2 years for this item.</p>
 ) : (
 <div className="flex flex-col gap-2">
 {historyPicker.entries.map((entry, eidx) => (
 <button
 key={eidx}
 onClick={() => handleSelectHistoryRate(entry)}
 className="w-full text-left p-3 rounded-md border border-divider hover:border-accent hover:bg-accent/8 transition-colors"
 >
 <div className="flex justify-between items-center">
 <span className="text-[15px] font-medium text-text">{entry.date}</span>
 <span className="font-heading font-semibold text-lg text-accent-700">₹{entry.rate}</span>
 </div>
 <div className="flex justify-between items-center mt-1 text-sm text-neutral-700">
 <span className="truncate max-w-[150px]">
 {entry.origin === 'repack' ? 'Made in-house (Repack)' : (entry.supplier || 'Unknown supplier')}
 </span>
 <span>{entry.voucher_number || 'No voucher #'} · Qty {entry.qty ?? '—'} {entry.unit || ''}</span>
 </div>
 {entry.origin === 'app_post' && (
 <span className="inline-block mt-1.5 text-[10.5px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 border border-accent px-2 py-0.5 rounded">
 Pending Tally sync
 </span>
 )}
 {entry.origin === 'repack' && (
 <span className="inline-block mt-1.5 text-[10.5px] font-bold uppercase tracking-wider text-accent-700 bg-accent/8 border border-accent px-2 py-0.5 rounded">
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
 </>
 )}
 </div>
 );
}
