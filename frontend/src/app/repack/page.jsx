'use client';
import { useState, useEffect } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { Trash2, PlusCircle } from 'lucide-react';

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
      
      showToast(`Success: ${execData.message}`, 'success');
      
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
    <div className="p-4 max-w-2xl mx-auto pb-24">
      <h1 className="text-2xl font-bold mb-6 text-slate-800 dark:text-white">Repack & Assembly</h1>
      
      <form onSubmit={handleSubmit} className="space-y-6 bg-white dark:bg-slate-800 p-6 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
        
        <div className="flex gap-4">
          <div className="flex-1">
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Date</label>
            <Input 
              type="date"
              value={formData.date}
              onChange={(e) => setFormData({...formData, date: e.target.value})}
              required
              className="w-full"
            />
          </div>
          <div className="flex-1">
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Store / Godown</label>
            <Select
              value={formData.store_name}
              onChange={(e) => setFormData({...formData, store_name: e.target.value})}
              className="w-full"
              disabled={!!lockedStore}
            >
              <option value="">Select store...</option>
              {stores.map(s => (
                <option key={s.id} value={s.store_name}>{s.store_name}</option>
              ))}
            </Select>
            <p className="text-xs text-slate-400 mt-1">Only needed when producing — not when defining a new recipe below.</p>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Finished Product</label>
          <Select 
            value={formData.conversion_id}
            onChange={(e) => setFormData({...formData, conversion_id: e.target.value})}
            required
            className="w-full"
          >
            <option value="">Select finished product...</option>
            {conversions.map(c => {
               const hasRecipe = c.components && c.components.length > 0;
               return (
                 <option key={c.id} value={c.id}>
                   {c.finished_stock_item} {!hasRecipe ? '(No Recipe)' : ''} {c.status !== 'ACTIVE' ? '(Pending)' : ''}
                 </option>
               );
            })}
            <option value="new" className="font-semibold text-indigo-600">+ Create New Product</option>
          </Select>
        </div>

        {(formData.conversion_id === 'new' || isExistingWithoutRecipe) && (
          <div className="p-4 bg-slate-50 dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-700 space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                {isExistingWithoutRecipe ? 'Configure Missing Recipe' : 'Define a New Product'}
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                Name the item you&apos;re making, then list what goes into <strong>one</strong> of it.
              </p>
            </div>

            {formData.conversion_id === 'new' && (
              <div>
                <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">What are you making?</label>
                <Input
                  type="text"
                  placeholder="e.g. Beetroot Chips 300G"
                  value={formData.new_product_name}
                  onChange={(e) => setFormData({...formData, new_product_name: e.target.value})}
                  required
                  className="text-sm"
                />
              </div>
            )}

            <div className="pt-2">
              <label className="block text-xs font-bold text-slate-700 dark:text-slate-300 mb-1 uppercase tracking-wide">
                What goes into one {formData.new_product_name || selectedConversion?.finished_stock_item || 'unit'}?
              </label>
              <p className="text-xs text-slate-500 mb-2">For each ingredient, pick the item and how much of it is used.</p>
              <div className="space-y-3">
                {components.map((comp, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    {/* Input/Select both hardcode w-full internally, so a width
                        class passed via their own className prop can lose to
                        that default depending on Tailwind's generated class
                        order. Wrapping each in its own sized container makes
                        w-full resolve against a wrapper we actually control. */}
                    <div className="w-16 shrink-0">
                      <Input
                        type="number"
                        step="0.0001"
                        placeholder="Qty"
                        value={comp.quantity}
                        onChange={(e) => handleComponentChange(idx, 'quantity', e.target.value)}
                        required
                        className="text-sm"
                      />
                    </div>
                    <span className="text-xs font-mono w-8 text-slate-500 shrink-0">{comp.unit || '—'}</span>
                    <span className="text-xs text-slate-400 shrink-0">of</span>
                    <div className="flex-1 min-w-0">
                      <Select
                        value={comp.item_name}
                        onChange={(e) => handleComponentChange(idx, 'item_name', e.target.value)}
                        required
                        className="text-sm"
                      >
                        <option value="">Select ingredient...</option>
                        {stockItems.map(item => (
                          <option key={item.name} value={item.name}>{item.name}</option>
                        ))}
                      </Select>
                    </div>

                    <button type="button" onClick={() => removeComponent(idx)} className="text-red-400 hover:text-red-600 p-1" disabled={components.length === 1}>
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>

              <Button type="button" variant="secondary" onClick={addComponent} className="mt-3 text-xs flex items-center gap-1 text-indigo-600">
                <PlusCircle className="w-4 h-4" /> Add Another Ingredient
              </Button>
            </div>

            {components.some(c => c.item_name && c.quantity) && (
              <div className="p-3 bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700">
                <p className="text-xs text-slate-500">
                  Making <strong>1 Pcs of {formData.new_product_name || selectedConversion?.finished_stock_item || 'this product'}</strong> will use:
                </p>
                <ul className="text-sm font-mono text-slate-700 dark:text-slate-300 mt-1 space-y-0.5">
                  {components.filter(c => c.item_name && c.quantity).map((c, i) => (
                    <li key={i}>{c.quantity} {c.unit} — {c.item_name}</li>
                  ))}
                </ul>
              </div>
            )}

            <Button
              type="submit"
              disabled={loading || !formData.new_product_name || components.some(c => !c.item_name || !c.quantity)}
              className="w-full mt-4 py-3 text-sm shadow-sm"
            >
              {loading ? 'Saving...' : 'Save Recipe'}
            </Button>
          </div>
        )}

        {selectedConversion && !isExistingWithoutRecipe && (
          <>
            <div>
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Quantity Produced</label>
              <div className="flex items-center gap-2">
                <Input 
                  type="number"
                  step="0.001"
                  placeholder="e.g. 100"
                  value={formData.dest_qty}
                  onChange={(e) => setFormData({...formData, dest_qty: e.target.value})}
                  required
                  className="w-full text-lg font-medium"
                />
                <span className="font-mono text-sm text-slate-500">{selectedConversion.output_unit}</span>
              </div>
            </div>

            {formData.dest_qty && (
              <div className="p-4 bg-slate-50 dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-700 mt-4">
                <p className="text-sm text-slate-600 dark:text-slate-400 mb-3 font-bold uppercase tracking-wider">Production Summary</p>
                <div className="space-y-2 mb-3 border-b border-slate-200 dark:border-slate-700 pb-3">
                  <p className="font-semibold text-slate-800 dark:text-slate-200">
                    {formData.dest_qty} × {selectedConversion.finished_stock_item}
                  </p>
                  <p className="text-xs text-slate-500">Components to consume:</p>
                  <ul className="text-sm font-mono text-slate-600 dark:text-slate-400 space-y-1">
                    {selectedConversion.components?.map(c => (
                      <li key={c.id}>
                        {(parseFloat(formData.dest_qty) * c.quantity_per_finished_unit).toFixed(4)} {c.component_unit} — {c.component_item_name}
                      </li>
                    ))}
                  </ul>
                </div>
                <p className="text-xs text-slate-500">
                  Total Cost will be calculated automatically based on live Godown valuation in Tally.
                </p>
              </div>
            )}

            <Button 
              type="submit" 
              disabled={loading || !formData.store_name || !formData.conversion_id || !formData.dest_qty}
              className="w-full mt-6 py-4 text-lg shadow-sm bg-green-600 hover:bg-green-700 text-white"
            >
              {loading ? 'Processing...' : 'Repack & Post to Tally'}
            </Button>
          </>
        )}
      </form>
    </div>
  );
}
