'use client';
import { useState, useRef, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { useUI } from '@/context/UIContext';
import { UploadCloud, CheckCircle2, AlertCircle, ChevronRight, Trash2 } from "lucide-react";

export default function BankStatementInteractive() {
  const { showToast, showConfirmDialog, showActionSheet } = useUI();

  const [file, setFile] = useState(null);
  const [bankLedger, setBankLedger] = useState("Federal Bank Gulshan");
  const [password, setPassword] = useState("");
  
  const [transactions, setTransactions] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isPosting, setIsPosting] = useState(false);
  const [filterMode, setFilterMode] = useState('all');
  
  const [ledgerCache, setLedgerCache] = useState({});
  const [allRules, setAllRules] = useState({});
  const [showRules, setShowRules] = useState(false);
  const [editingRule, setEditingRule] = useState(null);
  const [quickRuleModal, setQuickRuleModal] = useState(null);
  const [isSavingRule, setIsSavingRule] = useState(false);
  const [deletingRuleId, setDeletingRuleId] = useState(null);
  const fileInputRef = useRef(null);

  const bankOptions = [
    "Federal Bank Gulshan",
    "Union Bank Mahagun 133",
    "Union Bank Vvip 2170"
  ];

  useEffect(() => {
    fetchAllRules();
    fetchLedgers();
  }, []);

  const fetchAllRules = async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/bank-statement/mappings");
      if (res.ok) {
        const data = await res.json();
        const grouped = {};
        data.forEach(mapping => {
          const bankName = mapping.bank_account_name;
          if (!grouped[bankName]) grouped[bankName] = [];
          grouped[bankName].push(mapping);
        });
        setAllRules(grouped);
      }
    } catch (e) {
      console.error("Failed to load all rules", e);
    }
  };

  const fetchLedgers = async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/bank-statement/ledgers");
      if (res.ok) setLedgerCache(await res.json());
    } catch (e) {
      console.error("Failed to load ledgers", e);
    }
  };

  const handleFileChange = (e) => {
    const selectedFile = e.target.files[0];
    if (selectedFile) setFile(selectedFile);
  };

  const handleAnalyze = async () => {
    if (!file) {
      showToast("Please select a file first.", 'error');
      return;
    }

    setIsProcessing(true);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("bank_ledger_name", bankLedger);
    if (password) fd.append("password", password);

    try {
      const res = await fetch("http://127.0.0.1:8000/api/bank-statement/upload", {
        method: "POST",
        body: fd
      });
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || "Extraction failed");
      }
      const data = await res.json();
      setTransactions(data.transactions);
      fetchAllRules();
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setIsProcessing(false);
    }
  };

  const createRule = async (keyword, ledger) => {
    try {
      await fetch(`http://127.0.0.1:8000/api/bank-statement/mappings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          bank: bankLedger, 
          keyword: keyword.trim(), 
          ledger: ledger 
        })
      });
      showToast(`Rule saved for "${keyword}"`);
      
      const savedKeyword = keyword.trim().toUpperCase();
      const newTxns = transactions.map(tx => {
        if (tx.unmapped && tx.raw_narration.toUpperCase().includes(savedKeyword)) {
          const ledgerData = ledgerCache[ledger.toLowerCase()];
          const requiresCC = ledgerData && typeof ledgerData === 'object' ? ledgerData.cost_centre : !!ledgerData;
          return {
            ...tx,
            ledger: ledger,
            unmapped: false,
            missing_cost_center: requiresCC && !tx.cost_center
          };
        }
        return tx;
      });
      setTransactions(newTxns);
      fetchAllRules();
    } catch (e) {
      showToast("Failed to save rule", 'error');
    }
  };

  const updateTransaction = (index, field, value) => {
    const newTxns = [...transactions];
    const tx = newTxns[index];
    
    if (field === 'ledger') {
      tx.ledger = value;
      tx.unmapped = false; // User manually selected something
      
      const ledgerData = ledgerCache[value.toLowerCase()];
      const requiresCC = ledgerData && typeof ledgerData === 'object' ? ledgerData.cost_centre : !!ledgerData;
      tx.missing_cost_center = requiresCC && !tx.cost_center;

      setTransactions(newTxns);
    } 
    else if (field === 'cost_center') {
      tx.cost_center = value;
      const ledgerData = ledgerCache[tx.ledger?.toLowerCase()];
      const requiresCC = ledgerData && typeof ledgerData === 'object' ? ledgerData.cost_centre : !!ledgerData;
      tx.missing_cost_center = requiresCC && !value;
      setTransactions(newTxns);
    }
  };

  const handleDeleteRule = async (mappingId, keyword) => {
    showConfirmDialog({
      title: "Delete this mapping rule?",
      message: `Are you sure you want to delete the mapping for "${keyword}"?`,
      confirmText: "Delete",
      cancelText: "Cancel",
      onConfirm: async () => {
        setDeletingRuleId(mappingId);
        try {
          const res = await fetch(`http://127.0.0.1:8000/api/bank-statement/mappings/${mappingId}`, {
            method: 'DELETE'
          });
          if (!res.ok) throw new Error("Delete failed");
          showToast(`Rule deleted`);
          fetchAllRules();
        } catch (e) {
          showToast("Failed to delete rule", 'error');
        } finally {
          setDeletingRuleId(null);
        }
      }
    });
  };

  const handleSaveRule = async () => {
    if (!editingRule.keyword || !editingRule.target_ledger) {
      showToast("Keyword and Target Ledger are required.", "error");
      return;
    }
    
    setIsSavingRule(true);
    const payload = {
      bank: bankLedger,
      keyword: editingRule.keyword.trim(),
      target_ledger: editingRule.target_ledger.trim(),
      cost_center: editingRule.cost_center ? editingRule.cost_center.trim() : null
    };

    try {
      const url = editingRule.id 
        ? `http://127.0.0.1:8000/api/bank-statement/mappings/${editingRule.id}`
        : `http://127.0.0.1:8000/api/bank-statement/mappings`;
      
      const method = editingRule.id ? "PUT" : "POST";
      
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      
      if (!res.ok) throw new Error("Failed to save rule");
      
      await fetchAllRules();
      setEditingRule(null);
      showToast(editingRule.id ? "Rule updated" : "Rule created", "success");
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setIsSavingRule(false);
    }
  };

  const handlePostToTally = async () => {
    const hasMissingCC = transactions.some(tx => tx.missing_cost_center && !tx.cost_center);
    if (hasMissingCC) {
      showToast("Please resolve all missing cost centers before posting.", 'error');
      return;
    }

    setIsPosting(true);
    try {
      const payload = {
        transactions,
        bank_ledger_name: bankLedger
      };

      const res = await fetch("http://127.0.0.1:8000/api/bank-statement/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Post failed");
      
      showToast(data.message);
      setTransactions([]);
      setFile(null);
      setPassword("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setIsPosting(false);
    }
  };

  const formatDate = (ds) => {
    if (!ds) return "";
    if (ds.length === 8) {
      return `${ds.substring(6,8)}/${ds.substring(4,6)}/${ds.substring(0,4)}`;
    }
    return ds;
  };

  if (transactions.length > 0) {
    const unmappedCount = transactions.filter(t => t.missing_cost_center && !t.cost_center).length;
    
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-36">
        <TopBar title="Review Statement" />
        <div className="max-w-md mx-auto p-4 space-y-4 mt-4">
          
          <Card className="flex flex-col gap-2 bg-white dark:bg-gray-800">
            <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider">Summary</h3>
            <div className="flex justify-between items-center">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{transactions.length} transactions</p>
              {unmappedCount > 0 ? (
                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-amber-600 bg-amber-50 dark:bg-amber-900/30 px-2 py-1 rounded">
                  {unmappedCount} action{unmappedCount !== 1 && 's'} needed
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-green-600 bg-green-50 dark:bg-green-900/30 px-2 py-1 rounded">
                  <CheckCircle2 className="w-3 h-3" /> Ready to push
                </span>
              )}
            </div>
          </Card>

          <div className="flex bg-gray-200 dark:bg-gray-800 p-1 rounded-lg">
            <button 
              onClick={() => setFilterMode('all')}
              className={`flex-1 py-1.5 text-xs font-bold rounded-md transition-colors ${filterMode === 'all' ? 'bg-white dark:bg-gray-700 shadow text-gray-900 dark:text-white' : 'text-gray-500 hover:text-gray-700'}`}
            >
              All
            </button>
            <button 
              onClick={() => setFilterMode('mapped')}
              className={`flex-1 py-1.5 text-xs font-bold rounded-md transition-colors ${filterMode === 'mapped' ? 'bg-white dark:bg-gray-700 shadow text-gray-900 dark:text-white' : 'text-gray-500 hover:text-gray-700'}`}
            >
              Mapped
            </button>
            <button 
              onClick={() => setFilterMode('unmapped')}
              className={`flex-1 py-1.5 text-xs font-bold rounded-md transition-colors ${filterMode === 'unmapped' ? 'bg-white dark:bg-gray-700 shadow text-gray-900 dark:text-white' : 'text-gray-500 hover:text-gray-700'}`}
            >
              Unmapped
            </button>
          </div>

          <div className="space-y-3">
            {transactions.map((tx, idx) => {
              if (filterMode === 'mapped' && tx.unmapped) return null;
              if (filterMode === 'unmapped' && !tx.unmapped) return null;
              
              const needsAction = tx.missing_cost_center && !tx.cost_center;
              
              return (
                <Card key={idx} className={`flex flex-col gap-2 p-3 ${needsAction ? 'border-amber-300 ring-1 ring-amber-300' : 'border-gray-200 dark:border-gray-700'}`}>
                  <div className="flex justify-between items-start">
                    <p className="text-xs font-semibold text-gray-500">{formatDate(tx.date)}</p>
                    {tx.withdraw > 0 ? (
                      <p className="text-sm font-black text-red-600">- ₹ {tx.withdraw.toFixed(2)}</p>
                    ) : (
                      <p className="text-sm font-black text-green-600">+ ₹ {tx.deposit.toFixed(2)}</p>
                    )}
                  </div>
                  
                  <p className="text-sm font-medium text-gray-900 dark:text-white break-all leading-tight">{tx.raw_narration}</p>

                  <div className="mt-1 pt-2 border-t border-gray-100 dark:border-gray-800">
                    <div className="flex items-center gap-2">
                      <input 
                        type="text" 
                        list="ledger-options"
                        value={tx.ledger}
                        onChange={(e) => updateTransaction(idx, 'ledger', e.target.value)}
                        placeholder="Map to Ledger..."
                        className={`flex-1 text-sm p-1.5 border rounded-md focus:ring-2 outline-none transition-colors ${
                          tx.unmapped 
                            ? 'border-amber-200 bg-amber-50/50 dark:bg-amber-900/10 text-gray-900 dark:text-gray-100 focus:border-amber-500 focus:ring-amber-500/20' 
                            : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:border-teal-500 focus:ring-teal-500/20'
                        }`}
                      />
                      {tx.unmapped && <span className="text-[10px] font-bold text-amber-500 bg-amber-50 dark:bg-amber-900/30 px-1.5 py-1 rounded shrink-0">DEFAULT</span>}
                      {tx.ledger && (
                        <button 
                          onClick={() => setQuickRuleModal({ keyword: tx.raw_narration, target_ledger: tx.ledger, cost_center: tx.cost_center || '' })}
                          className="px-2 py-1.5 text-[10px] font-bold text-teal-600 bg-teal-50 hover:bg-teal-100 dark:bg-teal-900/30 dark:text-teal-400 rounded-md shrink-0 transition-colors"
                        >
                          + Rule
                        </button>
                      )}
                    </div>
                    
                    {tx.missing_cost_center && (
                      <div className="flex flex-col gap-1 mt-2">
                        <label className="text-[10px] font-bold text-red-400 uppercase tracking-wider flex items-center gap-1">
                          <AlertCircle className="w-3 h-3" /> Cost Center Required
                        </label>
                        <select
                          value={tx.cost_center || ''}
                          onChange={(e) => updateTransaction(idx, 'cost_center', e.target.value)}
                          className="w-full text-sm p-1.5 border border-red-300 bg-red-50 dark:bg-red-900/20 text-red-900 dark:text-red-100 rounded-md focus:border-red-500 focus:ring-2 focus:ring-red-500/20 outline-none"
                        >
                          <option value="">Select Store...</option>
                          <option value="Mahagun">Mahagun</option>
                          <option value="Vvip">Vvip</option>
                          <option value="Gulshan">Gulshan</option>
                        </select>
                      </div>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>

          <datalist id="ledger-options">
            {Object.keys(ledgerCache).map(l => <option key={l} value={l} />)}
          </datalist>
          
          <div className="fixed bottom-[80px] left-0 right-0 mx-auto max-w-md p-4 bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-800 z-10 flex gap-3 shadow-[0_-10px_15px_-3px_rgba(0,0,0,0.1)]">
            <Button variant="secondary" onClick={() => setTransactions([])} disabled={isPosting} className="flex-1">
              Cancel
            </Button>
            <Button onClick={handlePostToTally} disabled={isPosting || unmappedCount > 0} className="flex-1 bg-teal-600 hover:bg-teal-700 text-white">
              {isPosting ? "Sending..." : "Push to Tally"}
            </Button>
          </div>

          {quickRuleModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
              <Card className="w-full max-w-sm bg-white dark:bg-gray-800 shadow-2xl p-4 space-y-4">
                <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">Add Rule</h3>
                
                <Input 
                  label="Narration Contains"
                  value={quickRuleModal.keyword}
                  onChange={e => setQuickRuleModal({...quickRuleModal, keyword: e.target.value})}
                  placeholder="e.g. AMAZON, SWIGGY"
                />
                
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Maps to Ledger</label>
                  <input 
                    type="text" 
                    list="ledger-options"
                    value={quickRuleModal.target_ledger}
                    onChange={e => setQuickRuleModal({...quickRuleModal, target_ledger: e.target.value})}
                    placeholder="Select or type ledger..."
                    className="w-full text-sm p-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:border-teal-500 focus:ring-teal-500/20 outline-none transition-colors"
                  />
                </div>
                
                <Input 
                  label="Cost Center (Optional)"
                  value={quickRuleModal.cost_center || ''}
                  onChange={e => setQuickRuleModal({...quickRuleModal, cost_center: e.target.value})}
                  placeholder="e.g. Main Branch"
                />
                
                <div className="flex gap-3 pt-2">
                  <Button variant="secondary" onClick={() => setQuickRuleModal(null)} disabled={isSavingRule} className="flex-1">
                    Cancel
                  </Button>
                  <Button 
                    onClick={async () => {
                      setIsSavingRule(true);
                      try {
                        const payload = {
                          bank: bankLedger,
                          keyword: quickRuleModal.keyword.trim(),
                          target_ledger: quickRuleModal.target_ledger.trim(),
                          cost_center: quickRuleModal.cost_center ? quickRuleModal.cost_center.trim() : null
                        };
                        const res = await fetch(`http://127.0.0.1:8000/api/bank-statement/mappings`, {
                          method: 'POST',
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify(payload)
                        });
                        if (!res.ok) throw new Error("Failed to save rule");
                        await fetchAllRules();
                        
                        const keyword = quickRuleModal.keyword.trim().toLowerCase();
                        const targetLedger = quickRuleModal.target_ledger.trim();
                        const targetCC = quickRuleModal.cost_center ? quickRuleModal.cost_center.trim() : null;

                        const newTxns = [...transactions];
                        let updatedCount = 0;
                        newTxns.forEach(tx => {
                          if (tx.raw_narration.toLowerCase().includes(keyword)) {
                            tx.ledger = targetLedger;
                            tx.unmapped = false;
                            if (targetCC) tx.cost_center = targetCC;

                            const ledgerData = ledgerCache[targetLedger.toLowerCase()];
                            const requiresCC = ledgerData && typeof ledgerData === 'object' ? ledgerData.cost_centre : !!ledgerData;
                            tx.missing_cost_center = requiresCC && !tx.cost_center;
                            updatedCount++;
                          }
                        });
                        setTransactions(newTxns);

                        showToast(`Rule created and applied to ${updatedCount} transaction${updatedCount !== 1 ? 's' : ''}`, "success");
                        setQuickRuleModal(null);
                      } catch (e) {
                        showToast(e.message, "error");
                      } finally {
                        setIsSavingRule(false);
                      }
                    }} 
                    disabled={!quickRuleModal.keyword || !quickRuleModal.target_ledger || isSavingRule} 
                    className="flex-1"
                  >
                    {isSavingRule ? "Saving..." : "Save"}
                  </Button>
                </div>
              </Card>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (showRules) {
    const bankRules = allRules[bankLedger] || [];
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
        <TopBar title="Mapping Rules" showBack onBack={() => setShowRules(false)} />
        
        <div className="max-w-md mx-auto p-4 mt-4 space-y-4">
          <Select 
            label="Select Bank Account"
            value={bankLedger}
            onChange={e => setBankLedger(e.target.value)}
          >
            {bankOptions.map(b => <option key={b} value={b}>{b}</option>)}
          </Select>

          {editingRule ? (
            <Card className="bg-white dark:bg-gray-800 p-4 space-y-4 border-2 border-teal-100 dark:border-teal-900/30">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">
                {editingRule.id ? "Edit Rule" : "Add Rule"}
              </h3>
              
              <Input 
                label="Narration Contains"
                value={editingRule.keyword}
                onChange={e => setEditingRule({...editingRule, keyword: e.target.value})}
                placeholder="e.g. AMAZON, SWIGGY"
              />
              
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Maps to Ledger</label>
                <input 
                  type="text" 
                  list="ledger-options"
                  value={editingRule.target_ledger}
                  onChange={e => setEditingRule({...editingRule, target_ledger: e.target.value})}
                  placeholder="Select or type ledger..."
                  className="w-full text-sm p-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:border-teal-500 focus:ring-teal-500/20 outline-none transition-colors"
                />
              </div>

              <Input 
                label="Cost Center (Optional)"
                value={editingRule.cost_center || ''}
                onChange={e => setEditingRule({...editingRule, cost_center: e.target.value})}
                placeholder="e.g. Main Branch"
              />

              <div className="flex gap-3 pt-2">
                <Button variant="secondary" onClick={() => setEditingRule(null)} disabled={isSavingRule} className="flex-1">
                  Cancel
                </Button>
                <Button 
                  onClick={handleSaveRule} 
                  disabled={!editingRule.keyword || !editingRule.target_ledger || isSavingRule} 
                  className="flex-1"
                >
                  {isSavingRule ? (editingRule.id ? "Saving changes..." : "Saving...") : (editingRule.id ? "Save Changes" : "Save")}
                </Button>
              </div>
            </Card>
          ) : (
            <>
              {bankRules.length === 0 ? (
                <div className="text-center py-8">
                  <p className="text-sm font-semibold text-gray-500 mb-4">No rules found for this bank.</p>
                  <Button onClick={() => setEditingRule({ id: null, keyword: '', target_ledger: '', cost_center: '' })}>
                    + Add First Rule
                  </Button>
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="flex justify-between items-center px-1 mb-2">
                    <span className="text-xs font-bold text-gray-500 uppercase">{bankRules.length} Rule{bankRules.length !== 1 && 's'}</span>
                    <button 
                      onClick={() => setEditingRule({ id: null, keyword: '', target_ledger: '', cost_center: '' })}
                      className="text-xs font-bold text-teal-600 hover:text-teal-700 transition-colors"
                    >
                      + Add Rule
                    </button>
                  </div>
                  {bankRules.map(rule => (
                    <Card key={rule.id} className="flex justify-between items-center bg-white dark:bg-gray-800 p-3">
                      <div className="flex-1 pr-4 min-w-0">
                        <p className="text-sm font-bold text-gray-900 dark:text-white truncate">"{rule.keyword}"</p>
                        <p className="text-xs font-semibold text-teal-600 mt-1 truncate">{rule.target_ledger}</p>
                        {rule.cost_center && (
                          <p className="text-[10px] font-bold text-gray-400 uppercase mt-1">CC: {rule.cost_center}</p>
                        )}
                      </div>
                      <div className="flex gap-2">
                        <button 
                          onClick={() => setEditingRule({ id: rule.id, keyword: rule.keyword, target_ledger: rule.target_ledger, cost_center: rule.cost_center || '' })}
                          disabled={deletingRuleId === rule.id}
                          className="text-xs font-bold text-teal-600 hover:text-teal-700 bg-teal-50 hover:bg-teal-100 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                        >
                          Edit
                        </button>
                        <button 
                          onClick={() => handleDeleteRule(rule.id, rule.keyword)}
                          disabled={deletingRuleId === rule.id}
                          className="w-8 h-8 shrink-0 flex items-center justify-center rounded-lg text-red-500 bg-red-50 hover:bg-red-100 transition-colors disabled:opacity-50"
                        >
                          {deletingRuleId === rule.id ? (
                            <div className="w-3 h-3 rounded-full border-2 border-red-200 border-t-red-600 animate-spin" />
                          ) : (
                            <Trash2 className="w-4 h-4" />
                          )}
                        </button>
                      </div>
                    </Card>
                  ))}
                </div>
              )}
            </>
          )}
          
          <datalist id="ledger-options">
            {Object.keys(ledgerCache).map(l => <option key={l} value={l} />)}
          </datalist>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-20">
      <TopBar title="Import Bank Statement" showBack />
      
      <div className="max-w-md mx-auto p-4 mt-4 space-y-6">
        <Select 
          label="Select Bank Account"
          value={bankLedger}
          onChange={e => setBankLedger(e.target.value)}
        >
          {bankOptions.map(b => <option key={b} value={b}>{b}</option>)}
        </Select>

        <div className="bg-white dark:bg-gray-800 border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-2xl p-8 text-center transition-colors">
          <input
            type="file"
            accept=".pdf,.xlsx,.xls"
            className="hidden"
            id="file-upload"
            onChange={handleFileChange}
            ref={fileInputRef}
          />
          <label htmlFor="file-upload" className="cursor-pointer flex flex-col items-center">
            {isProcessing ? (
              <div className="w-12 h-12 rounded-full border-4 border-teal-200 border-t-teal-600 animate-spin mb-4" />
            ) : (
              <UploadCloud className="h-12 w-12 text-teal-600 mb-4" />
            )}
            <span className="text-lg font-bold text-gray-900 dark:text-gray-100">
              {isProcessing ? "Analyzing statement..." : (file ? file.name : "Choose File")}
            </span>
            {!file && <span className="text-sm text-gray-500 mt-1">Supports PDF & Excel</span>}
          </label>
        </div>

        <Input 
          label="PDF Password (Optional)"
          type="password"
          placeholder="Leave blank if not encrypted"
          value={password}
          onChange={e => setPassword(e.target.value)}
        />

        <div className="flex gap-3 mt-8">
          <Button variant="secondary" onClick={() => setShowRules(true)} className="flex-1">
            Manage Rules
          </Button>
          <Button onClick={handleAnalyze} disabled={!file || isProcessing} className="flex-1">
            {isProcessing ? "Analyzing..." : "Analyze"}
          </Button>
        </div>
      </div>
    </div>
  );
}
