'use client';

import { useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { ThemeProvider } from 'next-themes';
import { UIProvider } from '@/context/UIContext';
import { AuthProvider, useAuth } from '@/context/AuthContext';
import { SyncStatusProvider } from '@/context/SyncStatusContext';
import BottomNav from '@/components/layout/BottomNav';

const CHROMELESS_PATHS = ['/login', '/setup'];

// Reachable by logged-out visitors -- e.g. Meta's WhatsApp app review, or
// anyone just reading the privacy policy -- so must never be redirected to
// /login or /setup regardless of auth status.
const PUBLIC_PATHS = ['/privacy'];

// Focused single-task entry forms (reached via the "+" New Entry sheet) --
// full-screen flows in their own right, not a primary bottom-nav
// destination, so the tab bar would just sit on top of their own footer
// button. Unlike CHROMELESS_PATHS, an authed user visiting one of these is
// completely normal and must NOT be redirected away.
const NO_BOTTOM_NAV_PATHS = ['/sales', '/purchase', '/payment', '/transfer', '/stock-transfer', '/repack'];

function AuthGate({ children }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const isChromeless = CHROMELESS_PATHS.includes(pathname);
  const isPublic = PUBLIC_PATHS.includes(pathname);
  const hideBottomNav = isChromeless || NO_BOTTOM_NAV_PATHS.includes(pathname);

  useEffect(() => {
    if (isPublic) return;
    if (status === 'needs_setup' && pathname !== '/setup') {
      router.replace('/setup');
    } else if (status === 'guest' && pathname !== '/login') {
      router.replace('/login');
    } else if (status === 'authed' && isChromeless) {
      router.replace('/dashboard');
    }
  }, [status, pathname, isChromeless, isPublic, router]);

  // Public paths render unconditionally and immediately -- no waiting on
  // the auth check at all, since a logged-out visitor is the normal case.
  if (isPublic) {
    return <>{children}</>;
  }

  if (status === 'loading') {
    return <div className="min-h-screen flex items-center justify-center bg-bg" />;
  }
  // Waiting on a redirect from the effect above -- render nothing rather
  // than flashing the target page's content for one frame.
  if (status === 'needs_setup' && pathname !== '/setup') return null;
  if (status === 'guest' && pathname !== '/login') return null;
  if (status === 'authed' && isChromeless) return null;

  return (
    <>
      {children}
      {status === 'authed' && !hideBottomNav && <BottomNav />}
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
