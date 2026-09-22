'use client';
import { useState, useEffect } from "react";
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { Settings as SettingsIcon, Users, Trash2, UserPlus, KeyRound, Sparkles } from "lucide-react";

function ItemMatchingAiSection() {
  const { showToast } = useUI();
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/settings/item-matching-ai")
      .then(res => res.json())
      .then(data => setEnabled(!!data.enabled))
      .catch(() => { })
      .finally(() => setLoading(false));
  }, []);

  const handleToggle = async () => {
    const next = !enabled;
    setEnabled(next);
    setSaving(true);
    try {
      const res = await fetch("/api/settings/item-matching-ai", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!res.ok) throw new Error("Failed to save setting");
      showToast(next ? "AI item matching turned on" : "AI item matching turned off");
    } catch (err) {
      setEnabled(!next);
      showToast(err.message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-teal-600" /> AI Item Matching
        </h2>
        <p className="text-sm text-gray-500 mt-1">
          When an item extracted from an invoice doesn't exactly match anything in your Tally stock list, Gemini is asked to find the closest match. Turn this off to leave unmatched items for manual selection instead.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-gray-400">Loading...</p>
      ) : (
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-gray-900 dark:text-white">
            {enabled ? "On" : "Off"}
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            disabled={saving}
            onClick={handleToggle}
            className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors disabled:opacity-50 ${enabled ? "bg-teal-600" : "bg-gray-300 dark:bg-gray-700"
              }`}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${enabled ? "translate-x-6" : "translate-x-1"
                }`}
            />
          </button>
        </div>
      )}
    </Card>
  );
}

function TeamSection() {
  const { showToast, showConfirmDialog } = useUI();
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState([]);
  const [stores, setStores] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: "", password: "", store_name: "", is_owner: false });
  const [creating, setCreating] = useState(false);
  const [resettingId, setResettingId] = useState(null);
  const [resetPassword, setResetPassword] = useState("");
  const [resetting, setResetting] = useState(false);

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
    if (!form.name.trim() || !form.password || (!form.is_owner && !form.store_name)) {
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
      setForm({ name: "", password: "", store_name: "", is_owner: false });
      loadUsers();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setCreating(false);
    }
  };

  const handleResetPassword = async (id) => {
    if (!resetPassword || resetPassword.length < 4) {
      showToast("Password must be at least 4 characters", "error");
      return;
    }
    setResetting(true);
    try {
      const res = await fetch(`/api/auth/users/${id}/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: resetPassword }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to reset password");
      showToast("Password reset -- they'll need to sign in again");
      setResettingId(null);
      setResetPassword("");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setResetting(false);
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
          Team member accounts can only see and post to the store they're assigned to. An account with full access can do everything you can, including managing this list.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-gray-400">Loading...</p>
      ) : users.length === 0 ? (
        <p className="text-sm text-gray-400">No accounts yet.</p>
      ) : (
        <div className="divide-y divide-gray-100 dark:divide-gray-800 -mx-4">
          {users.map(u => (
            <div key={u.id} className="px-4 py-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-bold text-gray-900 dark:text-white">
                    {u.name} {u.id === currentUser?.id && <span className="text-xs font-medium text-gray-400">(you)</span>}
                  </p>
                  <p className="text-xs text-gray-500">
                    {u.is_owner ? 'Owner, all stores' : u.store_name}
                  </p>
                </div>
                {u.id !== currentUser?.id && (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => { setResettingId(resettingId === u.id ? null : u.id); setResetPassword(""); }}
                      className="p-2 text-gray-400 hover:text-teal-600 dark:hover:text-teal-400 transition-colors"
                      aria-label={`Reset ${u.name}'s password`}
                    >
                      <KeyRound className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleRemove(u.id, u.name)}
                      className="p-2 text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition-colors"
                      aria-label={`Remove ${u.name}`}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
              {resettingId === u.id && (
                <div className="flex flex-col gap-2 mt-3">
                  <Input
                    label={`New password for ${u.name}`}
                    type="password"
                    value={resetPassword}
                    onChange={e => setResetPassword(e.target.value)}
                    placeholder="at least 4 characters"
                  />
                  <Button onClick={() => handleResetPassword(u.id)} disabled={resetting}>
                    {resetting ? "Saving..." : "Save"}
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <form onSubmit={handleCreate} className="flex flex-col gap-3 pt-4 border-t border-gray-100 dark:border-gray-800">
        <h3 className="text-sm font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <UserPlus className="w-4 h-4" /> Add a team member
        </h3>
        <Input label="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. Ravi" />
        <Input label="Password" type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} placeholder="at least 4 characters" />

        <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-200 dark:border-gray-700 cursor-pointer">
          <input
            type="checkbox"
            checked={form.is_owner}
            onChange={e => setForm({ ...form, is_owner: e.target.checked, store_name: "" })}
            className="w-4 h-4 mt-0.5 text-teal-600 rounded focus:ring-teal-500"
          />
          <span>
            <span className="block text-sm font-bold text-gray-900 dark:text-white">Give full access</span>
            <span className="block text-xs text-gray-500 mt-0.5">
              Same as your account &mdash; every store, plus the ability to add and remove other accounts. Otherwise they're limited to one store below.
            </span>
          </span>
        </label>

        {!form.is_owner && (
          <Select label="Store" value={form.store_name} onChange={e => setForm({ ...form, store_name: e.target.value })}>
            <option value="" disabled>Select store...</option>
            {stores.map(s => <option key={s} value={s}>{s}</option>)}
          </Select>
        )}

        <Button type="submit" disabled={creating} className="mt-1">
          {creating ? "Creating..." : "Add account"}
        </Button>
      </form>
    </Card>
  );
}

function ChangePasswordSection() {
  const { showToast } = useUI();
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm_password: "" });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.current_password || !form.new_password) {
      showToast("Fill in every field", "error");
      return;
    }
    if (form.new_password !== form.confirm_password) {
      showToast("New passwords don't match", "error");
      return;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/auth/change-password", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: form.current_password, new_password: form.new_password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to change password");
      showToast("Password changed");
      setForm({ current_password: "", new_password: "", confirm_password: "" });
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
        <KeyRound className="w-5 h-5 text-teal-600" /> Change Password
      </h2>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <Input label="Current password" type="password" value={form.current_password} onChange={e => setForm({ ...form, current_password: e.target.value })} />
        <Input label="New password" type="password" value={form.new_password} onChange={e => setForm({ ...form, new_password: e.target.value })} placeholder="at least 4 characters" />
        <Input label="Confirm new password" type="password" value={form.confirm_password} onChange={e => setForm({ ...form, confirm_password: e.target.value })} />
        <Button type="submit" disabled={saving} className="mt-1">
          {saving ? "Saving..." : "Change Password"}
        </Button>
      </form>
    </Card>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3 border-b border-gray-100 dark:border-gray-800 pb-4">
        <div className="bg-teal-50 dark:bg-teal-900/30 p-2.5 rounded-xl">
          <SettingsIcon className="w-6 h-6 text-teal-600" />
        </div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">App Settings</h1>
      </div>

      <ChangePasswordSection />
      {user?.is_owner && <ItemMatchingAiSection />}
      {user?.is_owner && <TeamSection />}
    </div>
  );
}
