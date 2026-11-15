import Link from "next/link";

export function LandingFooter() {
  const currentYear = new Date().getFullYear();

  return (
    <footer className="relative bg-[#050608] border-t border-white/[0.08] px-4 py-12 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6 pb-8 border-b border-white/[0.08]">
          {/* Brand & Subtitle */}
          <div className="flex items-center gap-3">
            <div className="flex size-7 items-center justify-center rounded-lg bg-emerald-500/20 border border-emerald-500/30">
              <span className="size-2 rotate-45 rounded-[1px] bg-emerald-400" />
            </div>
            <div>
              <span className="font-display text-sm font-bold tracking-wider text-white uppercase">
                Mera
              </span>
              <span className="block text-2xs text-zinc-400">
                Multi-Agent Social Intelligence &bull; Dispatch Ledger
              </span>
            </div>
          </div>

          {/* Quick Links including explicit /login */}
          <nav className="flex flex-wrap items-center gap-6 text-xs text-zinc-400" aria-label="Footer navigation">
            <Link href="/login" className="transition-colors hover:text-white">
              Sign In
            </Link>
            <Link href="/register" className="transition-colors hover:text-white">
              Launch Console
            </Link>
            <a href="#post-sets" className="transition-colors hover:text-white">
              Post Sets
            </a>
            <a href="#pipeline" className="transition-colors hover:text-white">
              11-Agent Architecture
            </a>
            <a href="#integrity" className="transition-colors hover:text-white">
              Approval Integrity
            </a>
            <a href="#stagger" className="transition-colors hover:text-white">
              Stagger Engine
            </a>
          </nav>
        </div>

        {/* Bottom Metadata & System Guarantees */}
        <div className="mt-8 flex flex-col sm:flex-row items-center justify-between gap-4 font-mono text-2xs text-zinc-500">
          <div>
            &copy; {currentYear} Mera. Self-hosted dispatch ledger. All rights reserved.
          </div>
          <div className="flex items-center gap-3">
            <span>POSTGRESQL RLS ENFORCED</span>
            <span>&bull;</span>
            <span>OFFICIAL APIS ONLY</span>
            <span>&bull;</span>
            <span>ZERO-LLM PUBLISHER</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
