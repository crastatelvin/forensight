import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { ThemeInit } from "@/components/theme-init";
import { AppShell } from "@/components/app-shell";
import "./globals.css";

const sans = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

const mono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "ForenSight",
    template: "%s · ForenSight",
  },
  description:
    "Digital forensic case management with auditable, sourced analysis. Every result carries its threshold, model version and limitations.",
};

/**
 * Applies the stored theme before first paint.
 *
 * A `useEffect` toggle would flash light-mode on every load for dark-mode users:
 * the server has no idea which theme is stored, so it renders light, then the
 * effect corrects it. This script runs synchronously in <head> before the body
 * paints, which is the only place the class can be set early enough.
 */
const themeScript = `
(function () {
  try {
    var stored = localStorage.getItem('forensight-theme');
    var dark = stored === 'dark' || (stored === null &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.classList.toggle('dark', dark);
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen antialiased">
        <a href="#main" className="skip-link">
          Skip to main content
        </a>
        <ThemeInit />
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
