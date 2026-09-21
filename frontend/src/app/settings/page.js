'use client';
import { useState, useEffect } from "react";
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { Settings as SettingsIcon, Save, Users, Trash2, UserPlus } from "lucide-react";

function TeamSection() {
  const { showToast, showConfirmDialog } = useUI();
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState([]);
  const [stores, setStores] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: "", username: "", password: "", store_name: "" });
  const [creating, setCreating] = useState(false);

  const loadUsers = () => {
    fetch("/api/auth/users")
      .then(res => res.json())
      .then(data => { setUsers(Array.isArray(data) ? data : []); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadUsers();
    fetch("/api/sales/metadata")
      .then(res => res.json())
      .then(data => setStores(data.stores || []))
      .catch(() => { });
  }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.name.trim() || !form.username.trim() || !form.password || !form.store_name) {
      showToast("Fill in every field", "error");
      return;
    }
    setCreating(true);
    try {
      const res = await fetch("/api/auth/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to create account");
      showToast(`${data.name}'s account created`);
      setForm({ name: "", username: "", password: "", store_name: "" });
      loadUsers();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setCreating(false);
    }
  };

  const handleRemove = (id, name) => {
    showConfirmDialog({
      title: `Remove ${name}?`,
      message: "They'll no longer be able to sign in. This can't be undone.",
      danger: true,
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/auth/users/${id}`, { method: "DELETE" });
          if (!res.ok) throw new Error("Failed to remove account");
          showToast("Account removed");
          loadUsers();
        } catch (err) {
          showToast(err.message, "error");
        }
      }
    });
  };

  return (
    <Card className="flex flex-col gap-6">
      <div>
        <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <Users className="w-5 h-5 text-teal-600" /> Team
        </h2>
        <p className="text-sm text-gray-500 mt-1">
          Staff accounts can only see and post to the store they're assigned to. Only your owner account can manage this list.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-gray-400">Loading...</p>
      ) : users.length === 0 ? (
        <p className="text-sm text-gray-400">No accounts yet.</p>
      ) : (
        <div className="divide-y divide-gray-100 dark:divide-gray-800 -mx-4">
          {users.map(u => (
            <div key={u.id} className="flex items-center justify-between px-4 py-3">
              <div>
                <p className="text-sm font-bold text-gray-900 dark:text-white">
                  {u.name} {u.id === currentUser?.id && <span className="text-xs font-medium text-gray-400">(you)</span>}
                </p>
                <p className="text-xs text-gray-500">
                  {u.username} &middot; {u.is_owner ? 'Owner, all stores' : u.store_name}
                </p>
              </div>
              {!u.is_owner && (
                <button
                  onClick={() => handleRemove(u.id, u.name)}
                  className="p-2 text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition-colors"
                  aria-label={`Remove ${u.name}`}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <form onSubmit={handleCreate} className="flex flex-col gap-3 pt-4 border-t border-gray-100 dark:border-gray-800">
        <h3 className="text-sm font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <UserPlus className="w-4 h-4" /> Add a staff account
        </h3>
        <Input label="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. Ravi" />
        <Input label="Username" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} placeholder="pick a username" autoCapitalize="none" />
        <Input label="Password" type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} placeholder="at least 4 characters" />
        <Select label="Store" value={form.store_name} onChange={e => setForm({ ...form, store_name: e.target.value })}>
          <option value="" disabled>Select store...</option>
          {stores.map(s => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Button type="submit" disabled={creating} className="mt-1">
          {creating ? "Creating..." : "Add account"}
        </Button>
      </form>
    </Card>
  );
}

export default function SettingsPage() {
  const { showToast } = useUI();
  const { user } = useAuth();
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

      {user?.is_owner && <TeamSection />}
    </div>
  );
}
