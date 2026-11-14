import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { Gallery } from "./gallery";

/*
 * Dev-only component gallery (docs/12 F2: a gallery + tests instead of
 * Storybook). Not in the rail, the palette or the sitemap; 404 in production.
 * Renders every component twice: once in paper (light), once in ink (dark),
 * via nested [data-theme] subtrees, so both themes are reviewed side by side
 * whatever the page theme is.
 */
export const metadata: Metadata = {
  title: "Component gallery",
  robots: { index: false, follow: false },
};

export default function ComponentGalleryPage() {
  if (process.env.NODE_ENV === "production") notFound();

  return (
    <main id="main" className="min-h-screen bg-surface text-fg">
      <header className="sticky top-0 z-20 flex h-[var(--topbar-h)] items-center gap-3 border-b border-border bg-[color-mix(in_srgb,var(--bg)_92%,transparent)] px-6 backdrop-blur-[6px] max-rail:px-3">
        <span className="font-label text-xs font-medium tracking-[0.08em] text-fg-subtle uppercase">Dev</span>
        <span aria-hidden="true" className="text-border-strong">
          /
        </span>
        <h1 className="text-lg">Component gallery</h1>
        <span className="truncate font-mono text-xs text-fg-subtle max-rail:hidden">docs/12 §3 F2 · demo content from mockups-v2</span>
        <span className="flex-1" />
        <ThemeToggle />
      </header>
      <div className="grid gap-0 wide:grid-cols-2">
        {(["light", "dark"] as const).map((theme) => (
          <div key={theme} data-theme={theme} className="min-w-0 bg-surface p-6 text-fg max-rail:p-4">
            <p className="mb-4 inline-flex items-center gap-2 rounded-sm border border-border-strong px-2 py-0.5 font-mono text-xs text-fg-muted">
              data-theme=&quot;{theme}&quot;
            </p>
            <Gallery />
          </div>
        ))}
      </div>
    </main>
  );
}
