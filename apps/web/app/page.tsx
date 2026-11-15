import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import Link from "next/link";
import { readSessionTokens } from "@/lib/session/cookies";
import { serverClient } from "@/lib/api/server";
import { me, listWorkspaces } from "@mera/sdk";
import { screenHref } from "@/lib/nav";

async function getAuthSession() {
  try {
    const tokens = readSessionTokens(await cookies());
    if (!tokens.accessToken) return null;
    const client = await serverClient();
    const [userRes, pageRes] = await Promise.all([
      me({ client }),
      listWorkspaces({ client }),
    ]);
    if (!userRes.response?.ok || !userRes.data) return null;
    return {
      user: userRes.data,
      workspaces: pageRes.data?.data || [],
    };
  } catch {
    return null;
  }
}

export default async function Home() {
  const session = await getAuthSession();
  if (session) {
    const first = session.workspaces[0];
    redirect(first ? screenHref(first.slug, "dashboard") : "/welcome");
  }

  return (
    <div className="min-h-screen bg-bg text-fg selection:bg-accent selection:text-accent-fg">
      {/* Navigation */}
      <header className="sticky top-0 z-40 border-b border-border bg-bg/90 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-2.5 font-label text-sm font-semibold tracking-[0.14em] uppercase text-fg">
            <i className="size-2.5 shrink-0 rotate-45 rounded-[1px] bg-accent" />
            <span>Mera</span>
          </div>

          <nav className="flex items-center gap-4">
            <Link
              href="/login"
              className="text-xs font-medium text-fg-muted transition-colors hover:text-fg"
            >
              Sign In
            </Link>
            <Link
              href="/register"
              className="inline-flex h-8 items-center justify-center rounded-xs bg-accent px-3.5 text-xs font-medium text-accent-fg transition-colors hover:bg-accent-hover"
            >
              Get Started
            </Link>
          </nav>
        </div>
      </header>

      {/* Hero Section */}
      <main>
        <section className="relative border-b border-border px-6 py-20 md:py-28">
          <div className="mx-auto max-w-4xl text-center">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-border bg-bg-raised px-3 py-1 font-mono text-xs text-fg-muted shadow-2xs">
              <span className="size-1.5 rounded-full bg-accent" />
              <span>Multi-Agent Social Intelligence</span>
            </div>

            <h1 className="font-display text-4xl font-semibold tracking-tight text-fg md:text-6xl">
              Social distribution on autopilot.
            </h1>

            <p className="mx-auto mt-6 max-w-2xl text-base text-fg-muted md:text-lg">
              Orchestrate autonomous research, drafting, editing, and compliance agents.
              Publish with surgical precision across LinkedIn, X, Threads, and Meta—backed by
              instant Telegram sign-offs.
            </p>

            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link
                href="/register"
                className="inline-flex h-10 items-center justify-center rounded-xs bg-accent px-5 text-sm font-medium text-accent-fg shadow-xs transition-colors hover:bg-accent-hover"
              >
                Launch Console
              </Link>
              <Link
                href="/login"
                className="inline-flex h-10 items-center justify-center rounded-xs border border-border bg-bg-raised px-5 text-sm font-medium text-fg transition-colors hover:bg-bg-hover"
              >
                Sign In
              </Link>
            </div>
          </div>
        </section>

        {/* Feature Grid */}
        <section className="border-b border-border px-6 py-16 md:py-24">
          <div className="mx-auto max-w-6xl">
            <div className="mb-12 max-w-xl">
              <span className="font-mono text-xs font-semibold tracking-wider text-accent uppercase">
                Architecture
              </span>
              <h2 className="mt-2 font-display text-2xl font-semibold text-fg md:text-3xl">
                Built for teams that ship without compromising voice.
              </h2>
            </div>

            <div className="grid gap-6 md:grid-cols-3">
              {/* Feature 1 */}
              <div className="rounded-xs border border-border bg-bg-raised p-6 shadow-2xs">
                <div className="mb-4 inline-flex size-9 items-center justify-center rounded-xs border border-border bg-bg-sunken font-mono text-xs font-semibold text-accent">
                  01
                </div>
                <h3 className="font-display text-base font-semibold text-fg">
                  Multi-Agent Drafting Swarm
                </h3>
                <p className="mt-2 text-sm text-fg-muted leading-relaxed">
                  Dedicated researcher, copywriter, editor, and hashtag agents collaborate on every piece of content, calibrated to each brand voice and pillar.
                </p>
              </div>

              {/* Feature 2 */}
              <div className="rounded-xs border border-border bg-bg-raised p-6 shadow-2xs">
                <div className="mb-4 inline-flex size-9 items-center justify-center rounded-xs border border-border bg-bg-sunken font-mono text-xs font-semibold text-accent">
                  02
                </div>
                <h3 className="font-display text-base font-semibold text-fg">
                  Human-in-the-Loop Approvals
                </h3>
                <p className="mt-2 text-sm text-fg-muted leading-relaxed">
                  One-tap approval cards delivered directly to Telegram and WhatsApp. Approve, revise, or reject posts from your phone in seconds.
                </p>
              </div>

              {/* Feature 3 */}
              <div className="rounded-xs border border-border bg-bg-raised p-6 shadow-2xs">
                <div className="mb-4 inline-flex size-9 items-center justify-center rounded-xs border border-border bg-bg-sunken font-mono text-xs font-semibold text-accent">
                  03
                </div>
                <h3 className="font-display text-base font-semibold text-fg">
                  Staggered Multi-Channel Queues
                </h3>
                <p className="mt-2 text-sm text-fg-muted leading-relaxed">
                  Procrastinate task workers stagger distribution across accounts, avoiding spam heuristics and platform rate limits automatically.
                </p>
              </div>

              {/* Feature 4 */}
              <div className="rounded-xs border border-border bg-bg-raised p-6 shadow-2xs">
                <div className="mb-4 inline-flex size-9 items-center justify-center rounded-xs border border-border bg-bg-sunken font-mono text-xs font-semibold text-accent">
                  04
                </div>
                <h3 className="font-display text-base font-semibold text-fg">
                  Model Context Protocol (MCP)
                </h3>
                <p className="mt-2 text-sm text-fg-muted leading-relaxed">
                  Connect local stdio and remote SSE MCP servers. Your agents search live data sources, product catalogs, and news feeds in real time.
                </p>
              </div>

              {/* Feature 5 */}
              <div className="rounded-xs border border-border bg-bg-raised p-6 shadow-2xs">
                <div className="mb-4 inline-flex size-9 items-center justify-center rounded-xs border border-border bg-bg-sunken font-mono text-xs font-semibold text-accent">
                  05
                </div>
                <h3 className="font-display text-base font-semibold text-fg">
                  Row-Level Security Multi-Tenancy
                </h3>
                <p className="mt-2 text-sm text-fg-muted leading-relaxed">
                  PostgreSQL Row-Level Security enforced at the driver level ensures ironclad tenant boundary isolation and cryptographic credential wrapping.
                </p>
              </div>

              {/* Feature 6 */}
              <div className="rounded-xs border border-border bg-bg-raised p-6 shadow-2xs">
                <div className="mb-4 inline-flex size-9 items-center justify-center rounded-xs border border-border bg-bg-sunken font-mono text-xs font-semibold text-accent">
                  06
                </div>
                <h3 className="font-display text-base font-semibold text-fg">
                  Offline Deterministic Sandbox
                </h3>
                <p className="mt-2 text-sm text-fg-muted leading-relaxed">
                  Full sandbox mode lets you simulate entire approval lifecycles, test platform webhooks, and evaluate agent variations without live keys.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Call to action */}
        <section className="px-6 py-16 text-center md:py-20">
          <div className="mx-auto max-w-2xl">
            <h2 className="font-display text-2xl font-semibold text-fg md:text-3xl">
              Ready to take control of your distribution?
            </h2>
            <p className="mt-3 text-sm text-fg-muted">
              Deploy workspaces, connect brand social channels, and invite your team.
            </p>
            <div className="mt-6 flex justify-center gap-3">
              <Link
                href="/register"
                className="inline-flex h-9 items-center justify-center rounded-xs bg-accent px-4 text-xs font-medium text-accent-fg shadow-xs transition-colors hover:bg-accent-hover"
              >
                Create Workspace
              </Link>
              <Link
                href="/login"
                className="inline-flex h-9 items-center justify-center rounded-xs border border-border bg-bg-raised px-4 text-xs font-medium text-fg transition-colors hover:bg-bg-hover"
              >
                Sign In
              </Link>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-border bg-bg-muted/50 px-6 py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 text-xs text-fg-muted sm:flex-row">
          <div className="flex items-center gap-2">
            <i className="size-2 shrink-0 rotate-45 rounded-[1px] bg-accent" />
            <span className="font-mono">Mera &bull; drtea.cloud</span>
          </div>
          <p>&copy; {new Date().getFullYear()} Mera. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
