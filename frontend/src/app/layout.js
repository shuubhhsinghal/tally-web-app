import { Cormorant_Garamond, Lora } from "next/font/google";
import "./globals.css";

const fontHeading = Cormorant_Garamond({
  variable: "--font-heading",
  weight: ["400", "600"],
  subsets: ["latin"],
});

const fontBody = Lora({
  variable: "--font-body",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

import { Providers } from "./Providers";

export const metadata = {
  title: "Accounting Web App",
  description: "Offline-first accounting middleware",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Accounting",
  },
  icons: {
    icon: [
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: "/icons/apple-touch-icon.png",
  },
};

export const viewport = {
  themeColor: "#171615",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${fontHeading.variable} ${fontBody.variable}`}>
      <body className="min-h-full flex flex-col transition-colors" suppressHydrationWarning>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
