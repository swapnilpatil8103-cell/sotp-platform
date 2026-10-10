import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// Self-hosted Inter (variable, latin subset, SIL OFL: see ./fonts/Inter-OFL.txt)
// so the build needs no network access.
const inter = localFont({
  src: "./fonts/InterVariable.woff2",
  variable: "--font-inter",
  weight: "100 900",
  display: "swap",
});

export const metadata: Metadata = {
  title: "SOTP Intelligence",
  description:
    "Institutional-style, SEC-data-driven company valuation platform.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="font-sans bg-background text-primary antialiased">
        {children}
      </body>
    </html>
  );
}
