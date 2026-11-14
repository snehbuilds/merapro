import type { ReactNode } from "react";

/*
 * Sign-in, registration and onboarding. No mockup exists for these (docs/12
 * F3 note), so they stay restrained: one centred panel on the paper, the
 * typographic lockup from the rail, no illustration.
 */
export default function EntryLayout({ children }: { children: ReactNode }) {
  return (
    <main id="main" tabIndex={-1} className="grid min-h-screen place-items-center px-4 py-11 outline-none">
      <div className="grid w-full max-w-[400px] gap-6">
        <div
          aria-hidden="true"
          className="flex items-center justify-center gap-2 font-label text-md font-semibold tracking-[0.14em] text-fg uppercase"
        >
          <i className="size-[9px] shrink-0 rotate-45 rounded-[1px] bg-accent" />
          Mera
        </div>
        {children}
      </div>
    </main>
  );
}
