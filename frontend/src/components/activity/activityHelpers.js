import { Receipt, MoveRight, Building, Package, Landmark } from 'lucide-react';

export const getStatus = (dbStatus) => {
  if (dbStatus === 'PENDING') return 'waiting';
  if (dbStatus === 'SYNCED') return 'synced';
  return 'failed';
};

export const getIconType = (opType) => {
  if (!opType) return 'sale';
  if (opType.includes('VOUCHER')) return 'sale';
  if (opType.includes('LEDGER') || opType.includes('ITEM')) return 'bank';
  return 'sale';
};

export const getIcon = (type) => {
  switch (type) {
    case 'sale': return <Receipt className="w-5 h-5 text-blue-500" />;
    case 'purchase': return <Package className="w-5 h-5 text-purple-500" />;
    case 'stock': return <MoveRight className="w-5 h-5 text-indigo-500" />;
    case 'bank': return <Landmark className="w-5 h-5 text-teal-500" />;
    case 'transfer': return <Building className="w-5 h-5 text-green-500" />;
    default: return <Receipt className="w-5 h-5 text-gray-500" />;
  }
};
