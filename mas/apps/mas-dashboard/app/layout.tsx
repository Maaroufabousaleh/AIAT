import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MAS Dashboard",
  description: "AIAT Multi-Agent System Monitor",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
