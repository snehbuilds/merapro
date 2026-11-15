import Link from "next/link";

export function McpGovernanceSection() {
  return (
    <section id="architecture" className="relative overflow-hidden py-20 sm:py-28 px-4 sm:px-6 lg:px-8 bg-[#07080a] border-b border-white/[0.08]">
      {/* Background glow */}
      <div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 w-[700px] h-[350px] bg-gradient-to-r from-emerald-500/15 via-teal-500/10 to-cyan-500/15 blur-[140px] pointer-events-none rounded-full"
        aria-hidden="true"
      />

      <div className="relative mx-auto max-w-7xl">
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-2xs font-mono text-emerald-400">
            <span>ENTERPRISE GOVERNANCE</span>
          </div>
          <h2 className="mt-4 font-display text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight">
            Built for operators who demand total control.
          </h2>
          <p className="mt-3 text-base text-zinc-400 leading-relaxed">
            Mera is not a black-box hosted SaaS. It is self-hosted software built with
            PostgreSQL Row-Level Security, open protocol standards, and emergency circuit breakers.
          </p>
        </div>

        {/* 21st.dev Bento Grid */}
        <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {/* Card 1 */}
          <div className="rounded-2xl border border-white/10 bg-[#0c0d12]/90 p-6 backdrop-blur-xl flex flex-col justify-between transition-all hover:border-emerald-500/40 hover:shadow-[0_0_25px_rgba(16,185,129,0.15)] group">
            <div>
              <div className="font-mono text-2xs font-semibold text-emerald-400 uppercase tracking-wider">
                INTEGRATION
              </div>
              <h3 className="mt-3 font-display text-lg font-bold text-white group-hover:text-emerald-300 transition-colors">
                Model Context Protocol (MCP)
              </h3>
              <p className="mt-2 text-xs text-zinc-400 leading-relaxed">
                Connect external stdio or remote SSE MCP servers. Your agents ground drafts in proprietary internal documentation, product catalogs, and GitHub repos.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-white/[0.08] font-mono text-2xs text-zinc-400">
              SPEC: <span className="text-emerald-400 font-semibold">2026-07-28 &bull; stdio/SSE</span>
            </div>
          </div>

          {/* Card 2 */}
          <div className="rounded-2xl border border-white/10 bg-[#0c0d12]/90 p-6 backdrop-blur-xl flex flex-col justify-between transition-all hover:border-teal-500/40 hover:shadow-[0_0_25px_rgba(45,212,191,0.15)] group">
            <div>
              <div className="font-mono text-2xs font-semibold text-teal-400 uppercase tracking-wider">
                TENANCY
              </div>
              <h3 className="mt-3 font-display text-lg font-bold text-white group-hover:text-teal-300 transition-colors">
                PostgreSQL Row-Level Security
              </h3>
              <p className="mt-2 text-xs text-zinc-400 leading-relaxed">
                Tenant boundaries are enforced directly in the PostgreSQL kernel via Row-Level Security (RLS). Workspaces and API secrets never leak across tenants.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-white/[0.08] font-mono text-2xs text-zinc-400">
              ISOLATION: <span className="text-teal-400 font-semibold">Kernel RLS Enforced</span>
            </div>
          </div>

          {/* Card 3 */}
          <div className="rounded-2xl border border-white/10 bg-[#0c0d12]/90 p-6 backdrop-blur-xl flex flex-col justify-between transition-all hover:border-rose-500/40 hover:shadow-[0_0_25px_rgba(244,63,94,0.15)] group">
            <div>
              <div className="font-mono text-2xs font-semibold text-rose-400 uppercase tracking-wider">
                RESILIENCE
              </div>
              <h3 className="mt-3 font-display text-lg font-bold text-white group-hover:text-rose-300 transition-colors">
                Three-Tier Kill Switch
              </h3>
              <p className="mt-2 text-xs text-zinc-400 leading-relaxed">
                Emergency circuit breakers at the Workspace, Account Group, or single Account level. Instantly halts all background jobs and publishing dispatches.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-white/[0.08] font-mono text-2xs text-zinc-400">
              RESPONSE: <span className="text-rose-400 font-semibold">&lt; 100ms Halt</span>
            </div>
          </div>

          {/* Card 4 */}
          <div className="rounded-2xl border border-white/10 bg-[#0c0d12]/90 p-6 backdrop-blur-xl flex flex-col justify-between transition-all hover:border-cyan-500/40 hover:shadow-[0_0_25px_rgba(6,182,212,0.15)] group">
            <div>
              <div className="font-mono text-2xs font-semibold text-cyan-400 uppercase tracking-wider">
                SIMULATION
              </div>
              <h3 className="mt-3 font-display text-lg font-bold text-white group-hover:text-cyan-300 transition-colors">
                Deterministic Sandbox
              </h3>
              <p className="mt-2 text-xs text-zinc-400 leading-relaxed">
                Simulate entire multi-account approval lifecycles, evaluate agent output variations, and test bot sign-offs without live keys or platform rate limits.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-white/[0.08] font-mono text-2xs text-zinc-400">
              QUOTA COST: <span className="text-cyan-400 font-semibold">Zero Live Spends</span>
            </div>
          </div>
        </div>

        {/* 21st.dev Style Glowing CTA Banner */}
        <div className="relative mt-16 rounded-3xl border border-white/10 bg-gradient-to-b from-[#10131a] to-[#0a0c10] p-8 sm:p-14 text-center overflow-hidden shadow-[0_25px_60px_rgba(0,0,0,0.8)]">
          <div
            className="absolute -top-24 left-1/2 -translate-x-1/2 w-[600px] h-[250px] bg-emerald-500/20 blur-[120px] pointer-events-none rounded-full"
            aria-hidden="true"
          />

          <div className="relative max-w-2xl mx-auto">
            <h3 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-white leading-tight">
              Ready to take control of your social distribution?
            </h3>
            <p className="mt-4 text-base text-zinc-400 leading-relaxed">
              Deploy your workspace, connect your accounts, configure your Telegram approval bot, and start speaking as several identities with mathematical precision.
            </p>

            <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
              <Link
                href="/register"
                className="group relative inline-flex items-center justify-center gap-2 rounded-full bg-gradient-to-r from-emerald-400 via-teal-400 to-emerald-500 px-7 py-3.5 text-sm font-semibold text-black shadow-[0_0_25px_rgba(16,185,129,0.4)] hover:shadow-[0_0_35px_rgba(16,185,129,0.6)] transition-all hover:scale-[1.03]"
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
                className="inline-flex items-center justify-center gap-2 rounded-full border border-white/15 bg-white/[0.04] px-7 py-3.5 text-sm font-medium text-white hover:bg-white/[0.08] hover:border-white/25 transition-all shadow-[inset_0_1px_1px_rgba(255,255,255,0.08)]"
              >
                <span>Sign In to Existing Workspace</span>
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
