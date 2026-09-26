'use client';
import { useState, useEffect, useRef, useCallback } from "react";
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { Input } from '@/components/ui/Input';
import { MasterAutocomplete } from '@/components/ui/MasterAutocomplete';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { ClipboardList, Store as StoreIcon, Trash2, X } from 'lucide-react';

const money = (n) => `₹${(Number(n) || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

export default function StockCount() {
  const { showToast, showConfirmDialog } = useUI();
  const { user } = useAuth();
  const lockedStore = user && !user.is_owner ? user.store_name : null;

  const [meta, setMeta] = useState({ items: [], stores: [] });
  const [store, setStore] = useState(lockedStore || "");
  const [sessions, setSessions] = useState([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [session, setSession] = useState(null); // the active/open stock count, with its lines
  const [starting, setStarting] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [pickerValue, setPickerValue] = useState("");
  const [miscOpen, setMiscOpen] = useState(false);
  const [miscAmount, setMiscAmount] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const debounceTimers = useRef({});

  const loadSessions = useCallback(() => {
    setLoadingSessions(true);
    fetch('/api/stock-count')
      .then(res => res.json())
      .then(data => setSessions(Array.isArray(data) ? data : []))
      .catch(err => console.error("Failed to load stock counts", err))
      .finally(() => setLoadingSessions(false));
  }, []);

  useEffect(() => {
    fetch('/api/stock-count/metadata')
      .then(res => res.json())
      .then(data => setMeta(data))
      .catch(err => console.error("Failed to load metadata", err));
    loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    if (lockedStore) setStore(lockedStore);
  }, [lockedStore]);

  const openSession = (id) => {
    setMiscOpen(false);
    setMiscAmount("");
    setSearchQuery("");
    fetch(`/api/stock-count/${id}`)
      .then(res => res.json())
      .then(data => setSession(data))
      .catch(() => showToast("Failed to open stock count.", 'error'));
  };

  const handleStart = async () => {
    if (!store) {
      showToast("Pick a store first.", 'error');
      return;
    }
    setStarting(true);
    try {
      const res = await fetch('/api/stock-count', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ store })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to start stock count.");
      setMiscOpen(false);
      setMiscAmount("");
      setSearchQuery("");
      setSession(data);
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setStarting(false);
    }
  };

  const handleAddItem = async (itemName) => {
    setPickerValue("");
    if (!itemName || !session) return;
    if (session.lines.some(l => l.item_name.toLowerCase() === itemName.toLowerCase())) {
      showToast(`${itemName} is already in this count -- edit its quantity below.`);
      return;
    }
    try {
      const res = await fetch(`/api/stock-count/${session.id}/lines`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: itemName, qty: 1 })
      });
      const line = await res.json();
      if (!res.ok) throw new Error(line.detail || "Failed to add item.");
      setSession(prev => ({
        ...prev,
        lines: [...prev.lines, line],
        total_value: prev.total_value + line.amount
      }));
    } catch (err) {
      showToast(err.message, 'error');
    }
  };

  // "Misc" entries have no real item behind them -- just a number the user
  // wants counted into the total (e.g. loose odds and ends). Reuses the
  // normal add-line endpoint as qty=1 @ rate=<amount>, so it renders/edits
  // exactly like any other line. Each add gets its own auto-numbered label
  // ("Misc", "Misc 2", ...) since the backend upserts on item_name and we
  // want repeated misc entries to stack up as separate lines, not merge.
  const nextMiscName = () => {
    const existing = new Set(session.lines.map(l => l.item_name.toLowerCase()));
    if (!existing.has("misc")) return "Misc";
    let i = 2;
    while (existing.has(`misc ${i}`)) i++;
    return `Misc ${i}`;
  };

  const handleAddMisc = async () => {
    const amount = parseFloat(miscAmount);
    if (!amount || amount <= 0) {
      showToast("Enter a valid amount.", 'error');
      return;
    }
    try {
      const res = await fetch(`/api/stock-count/${session.id}/lines`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: nextMiscName(), qty: 1, rate: amount })
      });
      const line = await res.json();
      if (!res.ok) throw new Error(line.detail || "Failed to add misc amount.");
      setSession(prev => ({
        ...prev,
        lines: [...prev.lines, line],
        total_value: prev.total_value + line.amount
      }));
      setMiscAmount("");
    } catch (err) {
      showToast(err.message, 'error');
    }
  };

  const persistLine = (lineId, patch) => {
    fetch(`/api/stock-count/${session.id}/lines/${lineId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    })
      .then(res => res.json())
      .then(updated => {
        if (updated.detail) throw new Error(updated.detail);
        setSession(prev => {
          const lines = prev.lines.map(l => l.id === lineId ? updated : l);
          return { ...prev, lines, total_value: lines.reduce((sum, l) => sum + l.amount, 0) };
        });
      })
      .catch(err => showToast(err.message || "Failed to save.", 'error'));
  };

  const handleLineChange = (lineId, field, value) => {
    setSession(prev => {
      const lines = prev.lines.map(l => {
        if (l.id !== lineId) return l;
        const next = { ...l, [field]: value };
        next.amount = (Number(next.qty) || 0) * (Number(next.rate) || 0);
        return next;
      });
      return { ...prev, lines, total_value: lines.reduce((sum, l) => sum + l.amount, 0) };
    });

    clearTimeout(debounceTimers.current[lineId]);
    debounceTimers.current[lineId] = setTimeout(() => {
      persistLine(lineId, { [field]: Number(value) || 0 });
    }, 500);
  };

  const handleRemoveLine = (lineId) => {
    fetch(`/api/stock-count/${session.id}/lines/${lineId}`, { method: 'DELETE' })
      .then(res => {
        if (!res.ok) throw new Error("Failed to remove item.");
        setSession(prev => {
          const lines = prev.lines.filter(l => l.id !== lineId);
          return { ...prev, lines, total_value: lines.reduce((sum, l) => sum + l.amount, 0) };
        });
      })
      .catch(err => showToast(err.message, 'error'));
  };

  const handleComplete = () => {
    showConfirmDialog({
      title: "Complete stock count?",
      message: `This will lock in ${session.lines.length} item${session.lines.length === 1 ? '' : 's'} worth ${money(session.total_value)}. You won't be able to edit it afterwards.`,
      onConfirm: async () => {
        setCompleting(true);
        try {
          const res = await fetch(`/api/stock-count/${session.id}/complete`, { method: 'POST' });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || "Failed to complete stock count.");
          setSession(data);
          showToast("Stock count completed.");
        } catch (err) {
          showToast(err.message, 'error');
        } finally {
          setCompleting(false);
        }
      }
    });
  };

  const handleBackToList = () => {
    setSession(null);
    loadSessions();
  };

  const isReadOnly = session?.status === 'completed';
  const visibleLines = (isReadOnly && searchQuery.trim())
    ? session.lines.filter(l => l.item_name.toLowerCase().includes(searchQuery.trim().toLowerCase()))
    : (session?.lines || []);

  // --- Active session (counting or completed summary) ---
  if (session) {
    return (
      <div className="min-h-screen bg-bg pb-20">
        <TopBar title={isReadOnly ? "Count summary" : "Count stock"} showBack onBack={handleBackToList} />

        <div className="max-w-md mx-auto p-4 space-y-4 mt-2">
          <Card className="flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold text-text/60 uppercase">{session.store}</p>
              <p className="text-2xl font-heading font-semibold text-text">{money(session.total_value)}</p>
              <p className="text-xs text-neutral-600 mt-0.5">{session.lines.length} item{session.lines.length === 1 ? '' : 's'} counted</p>
            </div>
            {isReadOnly && (
              <span className="text-xs font-semibold text-neutral-700 border border-divider rounded-full px-2.5 py-1">Completed</span>
            )}
          </Card>

          {!isReadOnly && (
            <div className="space-y-2">
              <MasterAutocomplete
                value={pickerValue}
                onChange={handleAddItem}
                onCreate={handleAddItem}
                placeholder="Add item to count..."
                confirmed={meta.items}
                createLabel="item"
                icon={ClipboardList}
                inputClassName="w-full flex items-center gap-2.5 min-h-[56px] border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body shadow-sm"
              />

              {miscOpen ? (
                <Card className="flex items-center gap-2">
                  <input
                    type="number"
                    step="0.01"
                    autoFocus
                    placeholder="Amount"
                    value={miscAmount}
                    onChange={(e) => setMiscAmount(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleAddMisc(); }}
                    className="flex-1 min-h-[44px] px-3 rounded-md border border-divider bg-transparent text-text focus:outline-none focus-visible:border-accent"
                  />
                  <button
                    onClick={handleAddMisc}
                    className="min-h-[44px] px-4 rounded-md border border-accent text-accent-700 font-heading font-semibold hover:bg-accent/12 transition-colors"
                  >
                    Add
                  </button>
                  <button
                    onClick={() => { setMiscOpen(false); setMiscAmount(""); }}
                    className="text-neutral-500 hover:text-accent-700 shrink-0 p-1.5"
                    aria-label="Cancel"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </Card>
              ) : (
                <button
                  onClick={() => setMiscOpen(true)}
                  className="text-sm text-accent-700 font-heading font-semibold underline underline-offset-4 px-1"
                >
                  + Add misc amount
                </button>
              )}
            </div>
          )}

          {isReadOnly && session.lines.length > 0 && (
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search counted items..."
            />
          )}

          <div className="space-y-2">
            {session.lines.length === 0 && (
              <p className="text-sm text-neutral-600 text-center py-8">No items counted yet.</p>
            )}
            {isReadOnly && session.lines.length > 0 && visibleLines.length === 0 && (
              <p className="text-sm text-neutral-600 text-center py-8">No items match &quot;{searchQuery}&quot;.</p>
            )}
            {visibleLines.map(line => (
              <Card key={line.id} className="flex flex-col gap-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-heading font-semibold text-text leading-tight break-words">{line.item_name}</p>
                    {line.uom && <p className="text-xs text-neutral-600 mt-0.5">{line.uom}</p>}
                  </div>
                  {!isReadOnly && (
                    <button onClick={() => handleRemoveLine(line.id)} className="text-neutral-500 hover:text-accent-700 shrink-0 p-1">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>

                {isReadOnly ? (
                  <div className="flex justify-between text-sm">
                    <span className="text-neutral-600">{line.qty} &times; {money(line.rate)}</span>
                    <span className="font-semibold text-text">{money(line.amount)}</span>
                  </div>
                ) : (
                  <div className="flex gap-2 items-end">
                    <div className="flex-1">
                      <label className="text-[11px] text-text/60">Counted qty</label>
                      <input
                        type="number"
                        step="0.01"
                        value={line.qty}
                        onChange={(e) => handleLineChange(line.id, 'qty', e.target.value)}
                        className="w-full min-h-[40px] px-3 rounded-md border border-divider bg-transparent text-text focus:outline-none focus-visible:border-accent"
                      />
                    </div>
                    <div className="flex-1">
                      <label className="text-[11px] text-text/60">Rate</label>
                      <input
                        type="number"
                        step="0.01"
                        value={line.rate}
                        onChange={(e) => handleLineChange(line.id, 'rate', e.target.value)}
                        className="w-full min-h-[40px] px-3 rounded-md border border-divider bg-transparent text-text focus:outline-none focus-visible:border-accent"
                      />
                    </div>
                    <div className="flex-1 text-right pb-2.5">
                      <span className="font-semibold text-text">{money(line.amount)}</span>
                    </div>
                  </div>
                )}
              </Card>
            ))}
          </div>

          {!isReadOnly && session.lines.length > 0 && (
            <Button onClick={handleComplete} disabled={completing}>
              {completing ? "Completing..." : "Complete Count"}
            </Button>
          )}
          {isReadOnly && (
            <Button variant="secondary" onClick={handleBackToList}>Back to Stock Counts</Button>
          )}
        </div>
      </div>
    );
  }

  // --- Start screen + history ---
  return (
    <div className="min-h-screen bg-bg pb-20">
      <TopBar title="Stock Count" showBack />

      <div className="max-w-md mx-auto p-4 space-y-6 mt-2">
        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="font-heading font-semibold text-lg text-text">Start a new count</h2>
            <p className="text-sm text-neutral-600 mt-0.5">Count what's on the shelf and see what it's worth.</p>
          </div>

          {lockedStore ? (
            <p className="text-sm text-text/80">Store: <span className="font-semibold">{lockedStore}</span></p>
          ) : (
            <Select label="Store" icon={StoreIcon} value={store} onChange={(e) => setStore(e.target.value)}>
              <option value="">Select store...</option>
              {meta.stores.map(s => <option key={s} value={s}>{s}</option>)}
            </Select>
          )}

          <Button onClick={handleStart} disabled={starting || !store}>
            {starting ? "Starting..." : "Start Count"}
          </Button>
        </Card>

        <div className="space-y-2">
          <h3 className="text-xs font-bold text-neutral-600 uppercase tracking-widest px-1">Past counts</h3>
          {loadingSessions && <p className="text-sm text-neutral-600 px-1">Loading...</p>}
          {!loadingSessions && sessions.length === 0 && (
            <p className="text-sm text-neutral-600 px-1">No stock counts yet.</p>
          )}
          {sessions.map(s => (
            <Card key={s.id} onClick={() => openSession(s.id)} className="flex items-center justify-between">
              <div>
                <p className="font-heading font-semibold text-text">{s.store}</p>
                <p className="text-xs text-neutral-600 mt-0.5">
                  {new Date(s.started_at).toLocaleDateString()} &middot; {s.status === 'completed' ? 'Completed' : 'In progress'}
                </p>
              </div>
              <p className="font-semibold text-text">{money(s.total_value)}</p>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
