'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import TopBar from '@/components/layout/TopBar';
import { Card } from '@/components/ui/Card';
import { 
  TrendingUp, 
  ShoppingBag, 
  Users, 
  Book, 
  Activity, 
  Scale, 
  LineChart 
} from 'lucide-react';

export default function ReportsLandingPage() {
  const router = useRouter();

  const activeReports = [
    {
      title: 'Sales',
      description: 'Sales analysis',
      icon: TrendingUp,
      route: '/reporting/sales'
    },
    {
      title: 'Purchases',
      description: 'Purchase report',
      icon: ShoppingBag,
      route: '/reporting/purchases'
    },
    {
      title: 'Creditors',
      description: 'Supplier ledger',
      icon: Users,
      route: '/reporting/creditors'
    },
    {
      title: 'Day Book',
      description: 'All transactions',
      icon: Book,
      route: '/reporting/daybook'
    },
    {
      title: 'Profit & Loss',
      description: 'Financial result',
      icon: Activity,
      route: '/reporting/pl'
    }
  ];

  const futureReports = [
    {
      title: 'Balance Sheet',
      description: 'Coming soon',
      icon: Scale
    },
    {
      title: 'Collection Trend',
      description: 'Coming soon',
      icon: LineChart
    }
  ];

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 pb-20">
      <TopBar title="Reports" />
      
      <div className="max-w-md mx-auto p-4 space-y-6 mt-4">
        
        {/* Active Reports Grid */}
        <div className="grid grid-cols-2 gap-4">
          {activeReports.map((report) => {
            const Icon = report.icon;
            return (
              <Card 
                key={report.title} 
                className="flex flex-col gap-3 h-full cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 active:scale-95 transition-all"
                onClick={() => router.push(report.route)}
              >
                <div className="w-10 h-10 rounded-full bg-teal-50 dark:bg-teal-900/30 flex items-center justify-center">
                  <Icon className="w-5 h-5 text-teal-600 dark:text-teal-400" />
                </div>
                <div>
                  <h3 className="font-bold text-gray-900 dark:text-white mb-1 leading-tight">{report.title}</h3>
                  <p className="text-xs text-gray-500 leading-snug">{report.description}</p>
                </div>
              </Card>
            );
          })}
        </div>

        {/* Future Reports Section */}
        <div className="grid grid-cols-2 gap-4 mt-6">
          {futureReports.map((report) => {
            const Icon = report.icon;
            return (
              <Card 
                key={report.title} 
                className="flex flex-col gap-3 h-full opacity-60 bg-gray-50 dark:bg-gray-900 border-dashed"
              >
                <div className="w-10 h-10 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center">
                  <Icon className="w-5 h-5 text-gray-400" />
                </div>
                <div>
                  <h3 className="font-bold text-gray-700 dark:text-gray-300 mb-1 leading-tight">{report.title}</h3>
                  <p className="text-xs text-gray-500 leading-snug">{report.description}</p>
                </div>
              </Card>
            );
          })}
        </div>

      </div>
    </div>
  );
}
