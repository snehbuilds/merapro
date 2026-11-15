import Link from "next/link";

export function HeroSection() {
  return (
    <section className="relative overflow-hidden pt-28 pb-16 sm:pt-36 sm:pb-24 px-4 sm:px-6 lg:px-8 bg-[#060709] border-b border-white/[0.08]">
      {/* Background Ambient Radial Light Glows */}
      <div
        className="absolute -top-40 left-1/2 -translate-x-1/2 w-[900px] h-[450px] bg-gradient-to-r from-emerald-500/20 via-teal-500/20 to-cyan-500/15 blur-[140px] pointer-events-none rounded-full animate-glow-pulse"
        aria-hidden="true"
      />
      <div
        className="absolute top-1/3 -left-32 w-[400px] h-[400px] bg-emerald-600/10 blur-[120px] pointer-events-none rounded-full"
        aria-hidden="true"
      />
      <div
        className="absolute top-1/2 -right-32 w-[400px] h-[400px] bg-cyan-600/10 blur-[120px] pointer-events-none rounded-full"
        aria-hidden="true"
      />

      {/* Background Grid Pattern with Radial Mask */}
      <div
        className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff08_1px,transparent_1px),linear-gradient(to_bottom,#ffffff08_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_60%_50%_at_50%_20%,#000_70%,transparent_100%)] pointer-events-none"
        aria-hidden="true"
      />

      <div className="relative mx-auto max-w-5xl text-center">
        {/* Glowing Announcement Pill */}
        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5 text-xs font-mono text-emerald-300 shadow-[0_0_20px_rgba(16,185,129,0.25)] backdrop-blur-md">
          <span className="size-2 rounded-full bg-emerald-400 animate-ping" />
          <span className="font-semibold tracking-wide uppercase">Multi-Agent Social Intelligence &bull; v1.4</span>
        </div>

        {/* Display Headline */}
        <h1 className="mt-8 font-display text-4xl sm:text-6xl lg:text-7xl font-bold tracking-tight text-white leading-[1.08]">
          Speak as several identities at once.
          <span className="block mt-2 bg-gradient-to-r from-emerald-400 via-teal-300 to-cyan-300 bg-clip-text text-transparent">
            Approved in ten seconds from your phone.
          </span>
        </h1>

        {/* Subtitle */}
        <p className="mx-auto mt-6 max-w-2xl text-base sm:text-lg text-zinc-400 leading-relaxed font-sans">
          Mera monitors your industry feeds, synthesizes research, and crafts bespoke, platform-native post sets for every target account. No duplicate cross-posting. No unverified generative drift. Sign off via Telegram or WhatsApp with one tap before anything touches an API.
        </p>

        {/* Primary and Secondary Action Buttons */}
        <div className="mt-9 flex flex-wrap items-center justify-center gap-4">
          <Link
            href="/register"
            className="group relative inline-flex items-center justify-center gap-2 rounded-full bg-gradient-to-r from-emerald-400 via-teal-400 to-emerald-500 px-6 py-3 text-sm font-semibold text-black shadow-[0_0_25px_rgba(16,185,129,0.45)] hover:shadow-[0_0_35px_rgba(16,185,129,0.65)] transition-all hover:scale-[1.03]"
          >
            <span>Launch Console</span>
            <svg
              className="size-4 transition-transform group-hover:translate-x-1"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
            </svg>
          </Link>

          <Link
            href="/login"
            className="inline-flex items-center justify-center gap-2 rounded-full border border-white/15 bg-white/[0.04] px-6 py-3 text-sm font-medium text-white shadow-[inset_0_1px_1px_rgba(255,255,255,0.1)] hover:bg-white/[0.08] hover:border-white/25 transition-all"
          >
            <span>Sign In to Workspace</span>
          </Link>
        </div>

        {/* Micro-Proofs Bar */}
        <div className="mt-12 pt-8 border-t border-white/[0.08] flex flex-wrap items-center justify-center gap-x-8 gap-y-3 font-mono text-xs text-zinc-400">
          <div className="flex items-center gap-2">
            <span className="size-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
            <span>Single-use signed approval tokens</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="size-1.5 rounded-full bg-teal-400 shadow-[0_0_8px_rgba(45,212,191,0.8)]" />
            <span>Zero-LLM deterministic publisher</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="size-1.5 rounded-full bg-cyan-400 shadow-[0_0_8px_rgba(34,211,238,0.8)]" />
            <span>Per-account unique post sets</span>
          </div>
        </div>
      </div>
    </section>
  );
}
