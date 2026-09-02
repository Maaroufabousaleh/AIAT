import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";

export const metadata: Metadata = {
  title: "MAS Dashboard",
  description: "AIAT Multi-Agent System Monitor",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(() => { try { const key = "aiat-theme"; const stored = window.localStorage.getItem(key); const mode = stored === "light" || stored === "dark" || stored === "system" ? stored : "system"; const resolved = mode === "system" ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : mode; document.documentElement.dataset.theme = resolved; document.documentElement.style.colorScheme = resolved; } catch (_) { document.documentElement.dataset.theme = "dark"; document.documentElement.style.colorScheme = "dark"; } })();`,
          }}
        />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
