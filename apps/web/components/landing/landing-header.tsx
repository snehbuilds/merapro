import Link from "next/link";

export function LandingHeader() {
  return (
    <div className="fixed top-4 left-0 right-0 z-50 flex justify-center px-4 pointer-events-none">
      <header className="pointer-events-auto flex w-full max-w-5xl items-center justify-between rounded-full border border-white/10 bg-[#090b10]/85 px-4 sm:px-6 py-2 backdrop-blur-xl shadow-[0_12px_40px_rgba(0,0,0,0.7)] transition-all">
        {/* Brand Mark */}
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-2.5 group">
            <div className="relative flex size-7 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-400/20 to-teal-500/20 border border-emerald-500/30 shadow-[0_0_12px_rgba(16,185,129,0.3)]">
              <span className="size-2 rotate-45 rounded-[1px] bg-emerald-400 group-hover:scale-125 transition-transform" />
            </div>
            <span className="font-display text-sm font-bold tracking-wider text-white uppercase">
              Mera
            </span>
          </Link>
          <span className="hidden sm:inline-block h-3.5 w-px bg-white/10" aria-hidden="true" />
          <span className="hidden sm:inline-flex items-center gap-1.5 font-mono text-[11px] text-zinc-400">
            <span className="size-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span>Multi-Agent Dispatch</span>
          </span>
        </div>

        {/* Navigation Anchors */}
        <nav className="hidden md:flex items-center gap-5 text-xs font-medium text-zinc-400" aria-label="Landing page sections">
          <a href="#post-sets" className="transition-colors hover:text-white">
            Post Sets
          </a>
          <a href="#approval-bot" className="transition-colors hover:text-white">
            10s Sign-Off
          </a>
          <a href="#integrity" className="transition-colors hover:text-white">
            Approval Integrity
          </a>
          <a href="#pipeline" className="transition-colors hover:text-white">
            11-Agent Graph
          </a>
          <a href="#stagger" className="transition-colors hover:text-white">
            Stagger Engine
          </a>
        </nav>

        {/* Action Group including explicit /login */}
        <div className="flex items-center gap-2 sm:gap-3">
          <Link
            href="/login"
            className="text-xs font-medium text-zinc-300 hover:text-white transition-colors px-3 py-1.5 rounded-full hover:bg-white/5"
          >
            Sign In
          </Link>
          <Link
            href="/register"
            className="group relative inline-flex items-center justify-center gap-1.5 rounded-full bg-gradient-to-r from-emerald-400 via-teal-400 to-emerald-500 px-4 py-1.5 text-xs font-semibold text-black shadow-[0_0_20px_rgba(16,185,129,0.35)] hover:shadow-[0_0_25px_rgba(16,185,129,0.55)] transition-all hover:scale-[1.03]"
          >
            <span>Launch Console</span>
            <svg
              className="size-3.5 transition-transform group-hover:translate-x-0.5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
            </svg>
          </Link>
        </div>
      </header>
    </div>
  );
}
