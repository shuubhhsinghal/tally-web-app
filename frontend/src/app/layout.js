import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

import { Providers } from "./Providers";

export const metadata = {
  title: "Accounting Web App",
  description: "Offline-first accounting middleware",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-full flex flex-col bg-gray-50 dark:bg-gray-900 transition-colors" suppressHydrationWarning>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
