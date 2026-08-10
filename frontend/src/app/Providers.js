'use client';

import { ThemeProvider } from 'next-themes';
import { UIProvider } from '@/context/UIContext';
import BottomNav from '@/components/layout/BottomNav';

export function Providers({ children }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <UIProvider>
        {children}
        <BottomNav />
      </UIProvider>
    </ThemeProvider>
  );
}
