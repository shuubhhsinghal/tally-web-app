'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Lock, Eye, EyeOff, Cloud, CloudOff } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { useSyncStatus } from '@/context/SyncStatusContext';

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuth();
  const { isOnline } = useSyncStatus();
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const nameHasError = error.toLowerCase().includes('name');
  const pwHasError = error.toLowerCase().includes('password');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (loading) return;
    if (!name.trim()) { setError('Enter your name'); return; }
    if (password.length < 4) { setError('Password needs at least 4 characters'); return; }
    setError('');
    setLoading(true);
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Login failed.');
      login(data.token, data.user);
      router.replace('/dashboard');
    } catch (err) {
      setError(err.message || 'Login failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-6 py-8 bg-bg text-text">
      <form onSubmit={handleSubmit} className="w-full max-w-[380px] flex flex-col gap-4">
        <div className="flex flex-col items-center gap-1.5 text-center pb-2.5">
          <span className="w-16 h-16 grid place-items-center rounded-full mb-2 border border-accent text-accent-700">
            <Lock size={26} strokeWidth={1.5} />
          </span>
          <div className="text-[10.5px] tracking-[0.14em] uppercase text-accent-700">
            Shop Ledger
          </div>
          <div className="font-heading font-normal text-4xl leading-[1.1]">
            Mom&apos;s Pride
          </div>
          <div className="text-[13.5px] text-neutral-700">
            Sign in to continue
          </div>
        </div>

        <div className="h-px bg-divider" />

        <div className="flex flex-col gap-1.5">
          <label className="text-xs text-text/70">Your name</label>
          <input
            className={`w-full h-12 px-3 text-[15px] rounded-md bg-transparent outline-none border transition-colors hover:border-text/45 focus-visible:border-accent ${nameHasError ? 'border-accent' : 'border-divider'}`}
            value={name}
            onChange={e => { setName(e.target.value); if (error) setError(''); }}
            placeholder="e.g. Shubh"
            autoComplete="username"
            autoFocus
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs text-text/70">Password</label>
          <div className="relative">
            <input
              type={showPw ? 'text' : 'password'}
              className={`w-full h-12 px-3 pr-16 text-[15px] rounded-md bg-transparent outline-none border box-border transition-colors hover:border-text/45 focus-visible:border-accent ${pwHasError ? 'border-accent' : 'border-divider'}`}
              value={password}
              onChange={e => { setPassword(e.target.value); if (error) setError(''); }}
              placeholder="••••••••"
              autoComplete="current-password"
            />
            <button
              type="button"
              onClick={() => setShowPw(s => !s)}
              className="absolute right-1.5 top-1.5 h-9 px-2.5 text-[12.5px] rounded-md flex items-center gap-1 text-accent-700 transition-colors hover:bg-accent/10"
            >
              {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
              {showPw ? 'Hide' : 'Show'}
            </button>
          </div>
        </div>

        {error && (
          <div className="text-[13px] text-accent-800">{error}</div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="font-heading h-[50px] text-base rounded-md flex items-center justify-center border border-accent text-accent transition-colors hover:bg-accent/12 active:bg-accent/22 disabled:opacity-45 disabled:cursor-not-allowed"
        >
          {loading ? 'Signing in…' : 'Sign in'}
        </button>

        <div className="flex items-center justify-center gap-1.5 text-xs text-center text-neutral-700">
          {isOnline === false ? <CloudOff size={14} strokeWidth={1.8} /> : <Cloud size={14} strokeWidth={1.8} />}
          <span>{isOnline === false ? 'Offline · you can still sign in on this phone' : 'Connected · entries sync to Tally'}</span>
        </div>
      </form>
    </div>
  );
}
