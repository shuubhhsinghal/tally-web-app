'use client';

import { useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { ThemeProvider } from 'next-themes';
import { UIProvider } from '@/context/UIContext';
import { AuthProvider, useAuth } from '@/context/AuthContext';
import { SyncStatusProvider } from '@/context/SyncStatusContext';
import BottomNav from '@/components/layout/BottomNav';

const CHROMELESS_PATHS = ['/login', '/setup'];

function AuthGate({ children }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const isChromeless = CHROMELESS_PATHS.includes(pathname);

  useEffect(() => {
    if (status === 'needs_setup' && pathname !== '/setup') {
      router.replace('/setup');
    } else if (status === 'guest' && pathname !== '/login') {
      router.replace('/login');
    } else if (status === 'authed' && isChromeless) {
      router.replace('/dashboard');
    }
  }, [status, pathname, isChromeless, router]);

  if (status === 'loading') {
    return <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900" />;
  }
  // Waiting on a redirect from the effect above -- render nothing rather
  // than flashing the target page's content for one frame.
  if (status === 'needs_setup' && pathname !== '/setup') return null;
  if (status === 'guest' && pathname !== '/login') return null;
  if (status === 'authed' && isChromeless) return null;

  return (
    <>
      {children}
      {status === 'authed' && !isChromeless && <BottomNav />}
    </>
  );
}

export function Providers({ children }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <UIProvider>
        <AuthProvider>
          <SyncStatusProvider>
            <AuthGate>{children}</AuthGate>
          </SyncStatusProvider>
        </AuthProvider>
      </UIProvider>
    </ThemeProvider>
  );
}
