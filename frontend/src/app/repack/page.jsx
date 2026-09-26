'use client';
import { useState, useEffect } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { MasterAutocomplete } from '@/components/ui/MasterAutocomplete';
import TopBar from '@/components/layout/TopBar';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { Trash2, PlusCircle, Layers, Plus, ChevronDown } from 'lucide-react';

export default function RepackPage() {
  const { showToast } = useUI();
  const { user } = useAuth();
  const lockedStore = user && !user.is_owner ? user.store_name : null;
  const [loading, setLoading] = useState(false);
  const [stockItems, setStockItems] = useState([]);
  const [stores, setStores] = useState([]);
  const [conversions, setConversions] = useState([]);
  
  // Unified State
  const [formData, setFormData] = useState({
    // Deliberately NOT new Date().toISOString() alone -- that converts to
    // UTC, which silently shows yesterday's date for part of the day in any
    // timezone ahead of UTC (e.g. IST, roughly midnight-5:30am). Compensate
    // by the local timezone offset first, matching the same fix already used
    // in sales/payment/transfer/stock-transfer/purchase's own date defaults.
    date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().split('T')[0],
    store_name: '',
    conversion_id: '',
    dest_qty: '',

    // New Product Fields
    new_product_name: ''
  });

  const [components, setComponents] = useState([
    { item_name: '', unit: '', quantity: '' }
  ]);

  const fetchConversions = () => {
    return fetch('/api/repack/products')
      .then(res => res.json())
      .then(data => setConversions(data))
      .catch(err => console.error(err));
  };

  useEffect(() => {
    fetch('/api/repack/stock-items')
      .then(res => res.json())
      .then(data => {
        if (Array.isArray(data)) setStockItems(data);
      })
      .catch(err => console.error(err));

    fetch('/api/repack/stores')
      .then(res => res.json())
      .then(data => setStores(data))
      .catch(err => console.error(err));

    fetchConversions();
  }, []);

  useEffect(() => {
    if (lockedStore && formData.store_name !== lockedStore) {
      setFormData(prev => ({ ...prev, store_name: lockedStore }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedStore]);

  const handleComponentChange = (index, field, value) => {
    const newComps = [...components];
    newComps[index][field] = value;
    
    if (field === 'item_name') {
      const item = stockItems.find(i => i.name === value);
      if (item) {
        newComps[index].unit = item.base_unit.toUpperCase();
      }
    }
    
    setComponents(newComps);
  };

  const addComponent = () => {
    setComponents([...components, { item_name: '', unit: '', quantity: '' }]);
  };

  const removeComponent = (index) => {
    if (components.length > 1) {
      setComponents(components.filter((_, i) => i !== index));
    }
  };

  const handleCreateProduct = async () => {
    setLoading(true);
    try {
      const validComponents = components.filter(c => c.item_name && c.quantity);
      if (validComponents.length === 0) throw new Error("Please add at least one valid component.");

      const createRes = await fetch('/api/repack/product', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          new_product_name: formData.new_product_name,
          output_unit: 'PCS',
          components: validComponents.map(c => ({
            item_name: c.item_name,
            unit: c.unit,
            quantity: parseFloat(c.quantity)
          }))
        })
      });
      const createData = await createRes.json();
      if (!createRes.ok) throw new Error(createData.detail || 'Failed to create product recipe');
      
      showToast(`Created Recipe for: ${createData.name}`, 'success');
      
      setFormData({
        ...formData,
        conversion_id: createData.conversion_id.toString(),
        new_product_name: ''
      });
      setComponents([{ item_name: '', unit: '', quantity: '' }]);
      
      fetchConversions();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteRepack = async () => {
    setLoading(true);
    try {
      const execRes = await fetch('/api/repack/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          date: formData.date,
          store_name: formData.store_name,
          conversion_id: parseInt(formData.conversion_id),
          dest_qty: parseFloat(formData.dest_qty)
        })
      });
      const execData = await execRes.json();
      if (!execRes.ok) throw new Error(execData.detail || 'Failed to execute repack');

      if (execData.status === 'failed') {
        showToast(execData.message, 'error');
        return;
      }

      showToast(execData.message, 'success');

      setFormData({
        ...formData,
        conversion_id: '',
        dest_qty: ''
      });
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (formData.conversion_id === 'new' || isExistingWithoutRecipe) {
      handleCreateProduct();
    } else {
      handleExecuteRepack();
    }
  };

  const selectedConversion = formData.conversion_id !== 'new' && formData.conversion_id !== '' 
    ? conversions.find(c => c.id.toString() === formData.conversion_id) 
    : null;

  const isExistingWithoutRecipe = selectedConversion && (!selectedConversion.components || selectedConversion.components.length === 0);

  // When an existing product with no recipe is selected, use its name for creation
  useEffect(() => {
    if (isExistingWithoutRecipe) {
      setFormData(prev => ({...prev, new_product_name: selectedConversion.finished_stock_item}));
    }
  }, [isExistingWithoutRecipe, selectedConversion]);

  return (
    <div className="min-h-screen bg-bg pb-24">
      <TopBar 
        title="Repack & assembly" 
        showBack 
        rightContent={<span className="text-[11px] font-medium text-text/60 bg-text/5 px-2 py-1 rounded border border-divider">Stock journal voucher</span>}
      />
      
      <div className="max-w-md mx-auto p-4 mt-4">
        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="bg-surface p-4 rounded-xl border border-divider shadow-sm space-y-6">
            
            <div className="flex gap-4">
              <div className="flex-1">
                <label className="block text-sm font-medium text-text/70 mb-1.5">Date</label>
                <Input 
                  type="date"
                  value={formData.date}
                  onChange={(e) => setFormData({...formData, date: e.target.value})}
                  required
                  className="w-full"
                />
              </div>
              <div className="flex-1">
                <label className="block text-sm font-medium text-text/70 mb-1.5">Store / godown</label>
                <MasterAutocomplete
                  value={formData.store_name}
                  onChange={(val) => setFormData({...formData, store_name: val})}
                  placeholder="Select store"
                  confirmed={stores.map(s => s.store_name)}
                  createLabel="store"
                  inputClassName="w-full flex items-center gap-2.5 min-h-[48px] border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body shadow-sm"
                  disabled={!!lockedStore}
                />
                <p className="text-[11px] text-text/60 mt-2 leading-snug">Only needed when producing — not when defining a new recipe.</p>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-text/70 mb-1.5">Finished product</label>
              <div className="relative">
                <MasterAutocomplete
                  value={formData.conversion_id === 'new' ? '+ Create new product' : (selectedConversion?.finished_stock_item || '')}
                  onChange={(val) => {
                    const found = conversions.find(c => c.finished_stock_item === val);
                    if (found) {
                      setFormData({...formData, conversion_id: found.id.toString(), new_product_name: ''});
                    }
                  }}
                  placeholder="Select finished product"
                  confirmed={conversions.map(c => c.finished_stock_item)}
                  createLabel="product"
                  icon={Layers}
                  inputClassName="w-full flex items-center gap-2.5 min-h-[48px] pr-10 border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body shadow-sm"
                />
                <ChevronDown className="w-4 h-4 text-neutral-500 absolute right-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              </div>

              <button 
                type="button" 
                onClick={() => setFormData({...formData, conversion_id: 'new', new_product_name: ''})}
                className="flex items-center gap-2 mt-4 text-[14px] font-heading font-semibold text-accent-700 hover:text-accent-800 transition-colors"
              >
                <Plus className="w-4 h-4 shrink-0" />
                Create new product
              </button>
            </div>
          </div>
          
          <p className="text-[13px] text-text/60 text-center px-4">
            Pick what you&apos;re packing or assembling to see its recipe and how much stock it will use.
          </p>

        {(formData.conversion_id === 'new' || isExistingWithoutRecipe) && (
          <div className="p-5 bg-surface rounded-xl border border-accent shadow-sm space-y-6 mt-6">
            <div>
              <h3 className="text-[22px] font-heading font-semibold text-text">
                {isExistingWithoutRecipe ? 'Configure missing recipe' : 'Define a new product'}
              </h3>
              <p className="text-[13px] text-text/70 mt-1">
                Name the item you&apos;re making, then list what goes into <em>one</em> of it.
              </p>
            </div>

            {formData.conversion_id === 'new' && (
              <div>
                <label className="block text-[13px] font-medium text-text/60 mb-2">What are you making?</label>
                <Input
                  type="text"
                  placeholder="e.g. Beetroot Chips 300G"
                  value={formData.new_product_name}
                  onChange={(e) => setFormData({...formData, new_product_name: e.target.value})}
                  required
                  className="w-full h-[48px] bg-transparent text-[15px]"
                />
              </div>
            )}

            <div className="pt-2">
              <label className="block text-[10.5px] font-bold text-text/60 mb-1 uppercase tracking-[0.08em]">
                What goes into one {formData.new_product_name || selectedConversion?.finished_stock_item || 'unit'}?
              </label>
              <p className="text-[13px] text-text/70 mb-4">For each ingredient, pick the item and how much of it is used.</p>
              
              <div className="space-y-3">
                {components.map((comp, idx) => (
                  <div key={idx} className="flex items-center gap-3">
                    <div className="w-[72px] shrink-0">
                      <Input
                        type="number"
                        step="0.0001"
                        placeholder="Qty"
                        value={comp.quantity}
                        onChange={(e) => handleComponentChange(idx, 'quantity', e.target.value)}
                        required
                        className="h-11 text-center font-mono text-[14px]"
                      />
                    </div>
                    {comp.unit && (
                      <span className="text-[13px] font-mono text-text/60 shrink-0">
                        {comp.unit}
                      </span>
                    )}
                    <span className="text-[13px] text-text/60 shrink-0">
                      of
                    </span>
                    <div className="relative flex-1 min-w-0">
                      <MasterAutocomplete
                        value={comp.item_name}
                        onChange={(val) => handleComponentChange(idx, 'item_name', val)}
                        placeholder="Select ingredient"
                        confirmed={stockItems.map(item => item.name)}
                        createLabel="ingredient"
                        inputClassName="w-full flex items-center gap-2.5 h-11 pr-9 border border-divider rounded-md hover:border-text/45 transition-colors text-left bg-transparent text-text font-body text-[14px] shadow-sm"
                      />
                      <ChevronDown className="w-4 h-4 text-neutral-500 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                    </div>
                    <button type="button" onClick={() => removeComponent(idx)} className="text-text/40 hover:text-red-500 p-1 transition-colors shrink-0" disabled={components.length === 1}>
                      <Trash2 className="w-5 h-5" strokeWidth={1.5} />
                    </button>
                  </div>
                ))}
              </div>

              <button
                type="button"
                onClick={addComponent}
                className="w-full h-11 flex items-center justify-center gap-2 mt-4 rounded-md border border-dashed border-accent text-[14px] font-medium text-accent-700 hover:bg-accent/5 transition-colors"
              >
                <Plus className="w-4 h-4 shrink-0" />
                Add another ingredient
              </button>

              {(() => {
                const validRows = components.filter(c => c.item_name && c.quantity);
                if (validRows.length === 0) return null;
                const productLabel = formData.new_product_name || selectedConversion?.finished_stock_item || 'this item';
                return (
                  <div className="mt-5 p-4 rounded-lg border border-divider bg-bg">
                    <p className="text-[14px] text-text/80">
                      Making <span className="font-semibold text-text">1 PCS of {productLabel}</span> will use:
                    </p>
                    <ul className="text-[13px] font-mono text-text/70 space-y-1 mt-3">
                      {validRows.map((c, idx) => (
                        <li key={idx}>{c.quantity} {c.unit} — {c.item_name}</li>
                      ))}
                    </ul>
                  </div>
                );
              })()}
            </div>
          </div>
        )}

        {selectedConversion && !isExistingWithoutRecipe && (
          <div className="p-4 bg-surface rounded-xl border border-divider shadow-sm mt-6">
            <div>
              <label className="block text-[13px] font-medium text-text/70 mb-2">Quantity Produced</label>
              <div className="flex items-center gap-2">
                <Input 
                  type="number"
                  step="0.001"
                  placeholder="e.g. 100"
                  value={formData.dest_qty}
                  onChange={(e) => setFormData({...formData, dest_qty: e.target.value})}
                  required
                  className="w-full h-[48px] text-lg font-medium bg-transparent"
                />
                <span className="font-mono text-sm text-text/60 shrink-0">{selectedConversion.output_unit}</span>
              </div>
            </div>

            {formData.dest_qty && (
              <div className="mt-5 p-4 rounded-lg border border-divider bg-bg">
                <p className="text-[10.5px] text-text/60 mb-3 font-bold uppercase tracking-[0.08em]">Production Summary</p>
                <div className="space-y-2 mb-3 border-b border-divider pb-3">
                  <p className="font-semibold text-text">
                    {formData.dest_qty} × {selectedConversion.finished_stock_item}
                  </p>
                  <p className="text-xs text-text/60">Components to consume:</p>
                  <ul className="text-[13px] font-mono text-text/70 space-y-1">
                    {selectedConversion.components?.map(c => (
                      <li key={c.id}>
                        {(parseFloat(formData.dest_qty) * c.quantity_per_finished_unit).toFixed(4)} {c.component_unit} — {c.component_item_name}
                      </li>
                    ))}
                  </ul>
                </div>
                <p className="text-[11px] text-text/50">
                  Total Cost will be calculated automatically based on live Godown valuation in Tally.
                </p>
              </div>
            )}
          </div>
        )}

        <div className="fixed bottom-0 left-0 right-0 bg-surface border-t border-divider pb-safe z-40">
          <div className="p-4 flex flex-col gap-3 max-w-md mx-auto">
            {(formData.conversion_id === 'new' || isExistingWithoutRecipe) ? (
              <>
                <Button
                  type="submit"
                  disabled={loading || !formData.new_product_name || components.some(c => !c.item_name || !c.quantity)}
                  className="w-full h-[52px]"
                >
                  {loading ? 'Saving...' : 'Save recipe'}
                </Button>
                <button
                  type="button"
                  onClick={() => setFormData({...formData, conversion_id: '', new_product_name: ''})}
                  disabled={loading}
                  className="w-full h-[52px] flex items-center justify-center rounded-md font-heading font-semibold text-[17px] border border-divider text-text hover:bg-text/4 transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <Button 
                type="submit" 
                disabled={loading || !formData.store_name || !formData.conversion_id || !formData.dest_qty}
                className="w-full h-[52px]"
              >
                {loading ? 'Processing...' : 'Repack & Post to Tally'}
              </Button>
            )}
          </div>
        </div>
        </form>
      </div>
    </div>
  );
}
