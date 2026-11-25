import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";
import { THEME_PREPAINT_SCRIPT } from "@/lib/theme";
import "./globals.css";

/*
 * IBM Plex, self-hosted from the pinned @fontsource packages (the same Google
 * Fonts latin subsets, SIL OFL 1.1). next/font/local bundles them into the app:
 * no request to Google at build time or at runtime, so builds work offline and
 * behind a TLS-intercepting proxy (P2-M0.5), and the product stays self-hosted
 * (docs/12 F1.4). Weights match the mera.css @import.
 */
const plexSans = localFont({
  src: [
    { path: "../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-400-normal.woff2", weight: "400" },
    { path: "../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-500-normal.woff2", weight: "500" },
    { path: "../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-600-normal.woff2", weight: "600" },
  ],
  display: "swap",
  variable: "--font-plex-sans",
});

const plexCondensed = localFont({
  src: [
    {
      path: "../node_modules/@fontsource/ibm-plex-sans-condensed/files/ibm-plex-sans-condensed-latin-500-normal.woff2",
      weight: "500",
    },
    {
      path: "../node_modules/@fontsource/ibm-plex-sans-condensed/files/ibm-plex-sans-condensed-latin-600-normal.woff2",
      weight: "600",
    },
  ],
  display: "swap",
  variable: "--font-plex-condensed",
});

const plexMono = localFont({
  src: [
    { path: "../node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2", weight: "400" },
    { path: "../node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2", weight: "500" },
  ],
  display: "swap",
  variable: "--font-plex-mono",
});

export const metadata: Metadata = {
  title: { default: "Talonic", template: "%s · Talonic" },
  description: "Speak as several accounts at once, with approval on every post.",
  applicationName: "Talonic",
};

export const viewport: Viewport = {
  themeColor: "#f5f3ed",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // data-theme is set by the pre-paint script before hydration, so React must
    // not warn that the server-rendered <html> attributes differ.
    <html
      lang="en"
      suppressHydrationWarning
      className={`${plexSans.variable} ${plexCondensed.variable} ${plexMono.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_PREPAINT_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
