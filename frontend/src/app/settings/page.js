'use client';
import { useState, useEffect } from "react";
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useUI } from '@/context/UIContext';
import { Settings as SettingsIcon, Save } from "lucide-react";

export default function SettingsPage() {
  const { showToast } = useUI();
  const [method, setMethod] = useState("separate_ledger");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/settings-proxy")
      .then(res => res.json())
      .then(data => {
        if (data.gst_recording_method) {
          setMethod(data.gst_recording_method);
        }
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/settings-proxy", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "gst_recording_method", value: method })
      });
      if (!res.ok) throw new Error("Failed to save settings");
      showToast("Settings saved successfully");
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="p-8 animate-pulse text-gray-500">Loading settings...</div>;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3 border-b border-gray-100 dark:border-gray-800 pb-4">
        <div className="bg-teal-50 dark:bg-teal-900/30 p-2.5 rounded-xl">
          <SettingsIcon className="w-6 h-6 text-teal-600" />
        </div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">App Settings</h1>
      </div>

      <Card className="flex flex-col gap-6">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white">GST Recording Method</h2>
          <p className="text-sm text-gray-500 mt-1 mb-4">
            How should GST be recorded when pushing purchase vouchers to Tally?
          </p>

          <div className="space-y-3">
            <label className={`flex flex-col gap-1 p-4 border rounded-xl cursor-pointer transition-colors ${method === 'separate_ledger' ? 'border-teal-500 bg-teal-50/50 dark:bg-teal-900/10' : 'hover:bg-gray-50 dark:hover:bg-gray-800'}`}>
              <div className="flex items-center gap-3">
                <input type="radio" name="gst_recording" value="separate_ledger" checked={method === 'separate_ledger'} onChange={e => setMethod(e.target.value)} className="w-4 h-4 text-teal-600" />
                <span className="font-bold text-gray-900 dark:text-white">Separate Ledger (Paths A/B)</span>
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-400 pl-7">
                Item amounts are posted exclusive of GST. GST amounts are posted to separate Input CGST/SGST/IGST ledgers. This is the default approach.
              </p>
            </label>

            <label className={`flex flex-col gap-1 p-4 border rounded-xl cursor-pointer transition-colors ${method === 'included_in_rate' ? 'border-teal-500 bg-teal-50/50 dark:bg-teal-900/10' : 'hover:bg-gray-50 dark:hover:bg-gray-800'}`}>
              <div className="flex items-center gap-3">
                <input type="radio" name="gst_recording" value="included_in_rate" checked={method === 'included_in_rate'} onChange={e => setMethod(e.target.value)} className="w-4 h-4 text-teal-600" />
                <span className="font-bold text-gray-900 dark:text-white">Included in Item Rate (Paths C/D)</span>
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-400 pl-7">
                GST is absorbed into the item's purchase cost. The item rate will be inclusive of GST. No separate GST ledgers will be hit.
              </p>
            </label>
          </div>
        </div>

        <div className="flex justify-end pt-4 border-t border-gray-100 dark:border-gray-800">
          <Button onClick={handleSave} disabled={saving} className="min-w-[120px]">
            {saving ? "Saving..." : <><Save className="w-4 h-4 mr-2" /> Save Settings</>}
          </Button>
        </div>
      </Card>
    </div>
  );
}
