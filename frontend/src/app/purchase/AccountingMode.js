'use client';
import { useState, useEffect } from "react";
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { TextArea } from '@/components/ui/TextArea';
import { useUI } from '@/context/UIContext';
import { useRouter } from 'next/navigation';

export function AccountingMode({ onPostSuccess }) {
  const router = useRouter();
  const { showToast } = useUI();

  const [formData, setFormData] = useState({
    date: new Date().toISOString().split('T')[0],
    supplier: "",
    invoice_number: "",
    amount: "",
    cost_center: "",
    narration: ""
  });
  
  const [loading, setLoading] = useState(false);
  const [meta, setMeta] = useState({ suppliers: [], stores: ["Mahagun", "Vvip", "Gulshan"] });

  useEffect(() => {
    fetch("http://127.0.0.1:8000/api/purchase/metadata")
      .then(res => res.json())
      .then(data => setMeta(prev => ({...prev, suppliers: data.suppliers || []})))
      .catch(err => console.error("Failed to load metadata", err));
  }, []);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handlePost = async (e) => {
    e.preventDefault();
    setLoading(true);
    
    try {
      const finalNarration = formData.narration.trim() !== "" 
        ? formData.narration 
        : `Purchase Invoice ${formData.invoice_number} from ${formData.supplier}`;

      const payload = {
        supplier: formData.supplier,
        invoice_number: formData.invoice_number,
        amount: parseFloat(formData.amount),
        tally_date: formData.date.replace(/-/g, ''),
        narration: finalNarration,
        cost_center: formData.cost_center,
      };

      const response = await fetch("http://127.0.0.1:8000/api/purchase/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      
      if (response.ok) {
        showToast("Purchase saved");
        if (onPostSuccess) onPostSuccess();
        router.push('/dashboard');
      } else {
        showToast("Failed to post to Tally.", 'error');
      }
    } catch (error) {
      showToast("Network error while posting.", 'error');
    }
    setLoading(false);
  };

  return (
    <form onSubmit={handlePost} className="space-y-6">
      <Input 
        label="Date"
        type="date"
        name="date"
        value={formData.date}
        onChange={handleChange}
        required
      />

      <Select 
        label="Supplier"
        name="supplier"
        value={formData.supplier}
        onChange={handleChange}
        required
      >
        <option value="">Select supplier...</option>
        {meta.suppliers.map(s => (
          <option key={s} value={s}>{s}</option>
        ))}
      </Select>

      <Input 
        label="Total Amount"
        type="number"
        step="0.01"
        name="amount"
        placeholder="e.g. 50000"
        value={formData.amount}
        onChange={handleChange}
        required
      />

      <Select 
        label="Store"
        name="cost_center"
        value={formData.cost_center}
        onChange={handleChange}
        required
      >
        <option value="">Select store...</option>
        {meta.stores.map(s => (
          <option key={s} value={s}>{s}</option>
        ))}
      </Select>

      <Input 
        label="Invoice Number (Optional)"
        type="text"
        name="invoice_number"
        placeholder="e.g. INV-2024-001"
        value={formData.invoice_number}
        onChange={handleChange}
      />

      <TextArea 
        label="Notes (Optional)"
        name="narration"
        placeholder="Any extra details?"
        value={formData.narration}
        onChange={handleChange}
      />

      <Button type="submit" disabled={loading} className="mt-8">
        {loading ? "Saving..." : "Save purchase"}
      </Button>
    </form>
  );
}
