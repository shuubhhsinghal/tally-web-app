'use client';
import { useState, useEffect } from "react";
import TopBar from '@/components/layout/TopBar';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Toggle } from '@/components/ui/Toggle';
import { useUI } from '@/context/UIContext';
import { useAuth } from '@/context/AuthContext';
import { useRouter } from 'next/navigation';
import { Trash2, UserPlus, KeyRound, Sparkles, Cpu } from "lucide-react";

function SectionHeading({ icon: Icon, children }) {
  return (
    <h2 className="font-heading font-semibold text-xl flex items-center gap-2.5">
      <Icon className="w-5 h-5 text-accent-700" /> {children}
    </h2>
  );
}

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

  const handleToggle = async (next) => {
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
    <div className="flex flex-col gap-4 py-7 border-b border-divider">
      <div>
        <SectionHeading icon={Sparkles}>AI item matching</SectionHeading>
        <p className="text-sm text-neutral-700 mt-2">
          When an item read from an invoice doesn&apos;t exactly match anything in your Tally stock list, Gemini is asked to find the closest match. Turn this off to leave unmatched items for manual selection instead.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-neutral-600">Loading...</p>
      ) : (
        <div className="flex items-center justify-between pt-3 border-t border-divider">
          <span className="text-sm font-medium">
            {enabled ? "On" : "Off"}
          </span>
          <Toggle checked={enabled} onChange={handleToggle} disabled={saving} />
        </div>
      )}
    </div>
  );
}

const EXTRACTION_PROVIDERS = [
  { label: 'Gemini 3.5 Flash Lite', value: 'gemini' },
  { label: 'Qwen 3.5 Flash', value: 'qwen' },
];

function ExtractionProviderSection() {
  const { showToast } = useUI();
  const [provider, setProvider] = useState('gemini');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/settings/extraction-provider")
      .then(res => res.json())
      .then(data => setProvider(data.provider || 'gemini'))
      .catch(() => { })
      .finally(() => setLoading(false));
  }, []);

  const handleSelect = async (value) => {
    if (value === provider || saving) return;
    const prev = provider;
    setProvider(value);
    setSaving(true);
    try {
      const res = await fetch("/api/settings/extraction-provider", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: value }),
      });
      if (!res.ok) throw new Error("Failed to save setting");
      const label = EXTRACTION_PROVIDERS.find(p => p.value === value)?.label || value;
      showToast(`Invoice extraction now uses ${label}`);
    } catch (err) {
      setProvider(prev);
      showToast(err.message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 py-7 border-b border-divider">
      <div>
        <SectionHeading icon={Cpu}>Invoice extraction model</SectionHeading>
        <p className="text-sm text-neutral-700 mt-2">
          Which AI reads purchase invoices (supplier/tax metadata and item-wise rows) and bank statement PDFs. Switching takes effect on the very next upload &mdash; no restart needed.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-neutral-600">Loading...</p>
      ) : (
        <div className="grid grid-cols-2 border border-divider rounded-md overflow-hidden">
          {EXTRACTION_PROVIDERS.map(p => (
            <button
              key={p.value}
              onClick={() => handleSelect(p.value)}
              disabled={saving}
              className={`h-11 px-2 text-[13px] whitespace-nowrap transition-colors ${provider === p.value ? 'border border-accent text-accent-700 bg-accent/8 -m-px' : 'hover:bg-text/5'}`}
            >
              {p.label}
            </button>
          ))}
        </div>
      )}
    </div>
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
    <div className="flex flex-col py-7">
      {loading ? (
        <p className="text-sm text-neutral-600">Loading...</p>
      ) : users.length === 0 ? (
        <p className="text-sm text-neutral-600">No accounts yet.</p>
      ) : (
        <div>
          {users.map(u => (
            <div key={u.id} className="py-3.5 border-b border-divider">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-[15px]">
                    {u.name} {u.id === currentUser?.id && <span className="text-sm text-neutral-600">(you)</span>}
                  </p>
                  <p className="text-sm text-neutral-700 mt-0.5">
                    {u.is_owner ? 'Owner, all stores' : u.store_name}
                  </p>
                </div>
                {u.id !== currentUser?.id && (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => { setResettingId(resettingId === u.id ? null : u.id); setResetPassword(""); }}
                      className="p-2 text-neutral-600 hover:text-accent-700 transition-colors"
                      aria-label={`Reset ${u.name}'s password`}
                    >
                      <KeyRound className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleRemove(u.id, u.name)}
                      className="p-2 text-neutral-600 hover:text-red-600 transition-colors"
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

      <form onSubmit={handleCreate} className="flex flex-col gap-3 pt-6">
        <h3 className="font-heading font-semibold text-lg flex items-center gap-2">
          <UserPlus className="w-4 h-4 text-accent-700" /> Add a team member
        </h3>
        <Input label="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. Ravi" />
        <Input label="Password" type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} placeholder="at least 4 characters" />

        <label className="flex items-start gap-3 p-3 rounded-md border border-divider cursor-pointer">
          <input
            type="checkbox"
            checked={form.is_owner}
            onChange={e => setForm({ ...form, is_owner: e.target.checked, store_name: "" })}
            className="w-4 h-4 mt-0.5 accent-[var(--color-accent)] rounded"
          />
          <span>
            <span className="block text-[15px] font-medium">Give full access</span>
            <span className="block text-sm text-neutral-700 mt-0.5">
              Same as your account &mdash; every store, plus the ability to add and remove other accounts. Otherwise they&apos;re limited to one store below.
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
    </div>
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
    <div className="flex flex-col gap-4 py-7 border-b border-divider">
      <SectionHeading icon={KeyRound}>Change password</SectionHeading>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <Input label="Current password" type="password" value={form.current_password} onChange={e => setForm({ ...form, current_password: e.target.value })} />
        <Input label="New password" type="password" value={form.new_password} onChange={e => setForm({ ...form, new_password: e.target.value })} placeholder="at least 4 characters" />
        <Input label="Confirm new password" type="password" value={form.confirm_password} onChange={e => setForm({ ...form, confirm_password: e.target.value })} />
        <Button type="submit" disabled={saving} className="mt-1">
          {saving ? "Saving..." : "Change password"}
        </Button>
      </form>
    </div>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const router = useRouter();

  return (
    <div className="min-h-screen bg-bg pb-20">
      <TopBar title="Settings" kicker="Mom's Pride" showBack onBack={() => router.push('/dashboard')} />
      <div className="max-w-md mx-auto px-5">
        <ChangePasswordSection />
        {user?.is_owner && <ExtractionProviderSection />}
        {user?.is_owner && <ItemMatchingAiSection />}
        {user?.is_owner && <TeamSection />}
      </div>
    </div>
  );
}
