import { Receipt, ShoppingCart, ArrowDownLeft, ArrowUpRight, RotateCcw, Landmark } from 'lucide-react';

export const getStatus = (dbStatus) => {
  if (dbStatus === 'PENDING') return 'waiting';
  if (dbStatus === 'SYNCED') return 'synced';
  return 'failed';
};

// Distinguished by icon shape, not color, matching the design system's
// monochrome convention (color is reserved for the accent/attention cases).
// Mirrors the icon choices in the "New entry" sheet (BottomNav.js).
export const getIcon = (typeLabel) => {
  switch (typeLabel) {
    case 'Sale': return <Receipt className="w-5 h-5 text-neutral-700" />;
    case 'Purchase': return <ShoppingCart className="w-5 h-5 text-neutral-700" />;
    case 'Purchase return': return <RotateCcw className="w-5 h-5 text-neutral-700" />;
    case 'Payment': return <ArrowUpRight className="w-5 h-5 text-neutral-700" />;
    case 'Receipt': return <ArrowDownLeft className="w-5 h-5 text-neutral-700" />;
    case 'Loan received': return <Landmark className="w-5 h-5 text-neutral-700" />;
    default: return <Receipt className="w-5 h-5 text-neutral-700" />;
  }
};
