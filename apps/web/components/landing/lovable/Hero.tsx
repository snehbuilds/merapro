import { ArrowRight, Check, FileSearch, ShieldCheck, Send } from "./icons";
import { Button } from "@/components/ui/button";

const posts = [
  { p: "LinkedIn", t: "What the new grid-storage rules mean for mid-size utilities — three takeaways.", x: "left-[52%] top-[2%]" },
  { p: "X", t: "Grid storage just got a rulebook. The short version 🧵", x: "left-[58%] top-[30%]" },
  { p: "Threads", t: "Honestly the most interesting energy story this week…", x: "left-[52%] top-[58%]" },
];

export function Hero() {
  return (
    <section id="top" className="relative overflow-hidden">
      <div className="pointer-events-none absolute inset-0 bg-grid [mask-image:radial-gradient(ellipse_at_70%_30%,black,transparent_70%)]" aria-hidden />
      <div className="relative mx-auto grid max-w-7xl gap-14 px-5 pb-20 pt-16 sm:px-8 lg:grid-cols-[1.05fr_1fr] lg:pb-28 lg:pt-24">
        <div className="flex flex-col justify-center">
          <p className="animate-rise inline-flex w-fit items-center gap-2 rounded-full border border-border bg-card px-3 py-1 font-mono text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-success" /> Self-hosted multi-agent publishing
          </p>
          <h1 className="animate-rise mt-6 font-display text-5xl leading-[0.98] text-ink sm:text-6xl lg:text-7xl" style={{ ["--d" as string]: "80ms" }}>
            One story. Every account. <em className="text-gradient-accent">A voice of its own.</em>
          </h1>
          <p className="animate-rise mt-6 max-w-xl text-lg leading-relaxed text-muted-foreground" style={{ ["--d" as string]: "160ms" }}>
            Mera researches the stories in your industry feeds, writes a distinct, platform-native post set for every account you run, and waits for an explicit human sign-off before anything is published.
          </p>
          <div className="animate-rise mt-9 flex flex-wrap gap-3" style={{ ["--d" as string]: "240ms" }}>
            <Button asChild size="lg" className="group"><a href="/register">Launch console <ArrowRight className="transition-transform group-hover:translate-x-0.5" /></a></Button>
            <Button asChild size="lg" variant="outline"><a href="#product">Explore the system</a></Button>
          </div>
          <ol className="animate-rise mt-10 flex flex-wrap items-center gap-x-3 gap-y-2 font-mono text-xs text-muted-foreground" style={{ ["--d" as string]: "320ms" }}>
            {["Research", "Account post sets", "Human sign-off", "Controlled publish"].map((s, i) => (
              <li key={s} className="flex items-center gap-3">{i > 0 && <span className="text-border">—</span>}<span><span className="text-primary">0{i + 1}</span> {s}</span></li>
            ))}
          </ol>
        </div>
        <HeroComposition />
      </div>
    </section>
  );
}

function HeroComposition() {
  return (
    <div className="relative mx-auto aspect-[1/1.35] w-full sm:aspect-[1/1.02] max-w-[560px]" aria-label="Illustration: one researched story branching into account-specific posts awaiting approval" role="img">
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
        {[12, 40, 68].map((y) => (
          <path key={y} d={`M44 44 C 50 44, 48 ${y}, 54 ${y}`} fill="none" stroke="var(--primary)" strokeOpacity=".7" strokeWidth=".35" className="animate-dash" vectorEffect="non-scaling-stroke" />
        ))}
        <path d="M70 76 C 70 82, 60 84, 52 86" fill="none" stroke="var(--success)" strokeOpacity=".6" strokeWidth=".35" className="animate-dash" vectorEffect="non-scaling-stroke" />
      </svg>

      <div className="animate-rise absolute left-0 top-[22%] w-[46%] rounded-xl border border-border bg-card p-4 shadow-lift" style={{ ["--d" as string]: "200ms" }}>
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-primary"><FileSearch className="h-3.5 w-3.5" /> Research brief</div>
        <p className="mt-3 text-sm font-medium leading-snug text-ink">Regulator publishes first framework for grid-scale battery storage</p>
        <div className="mt-3 space-y-1.5">
          {["3 sources verified", "Key claims cited", "Angle: operators"].map((s) => (
            <div key={s} className="flex items-center gap-1.5 text-[11px] text-muted-foreground"><Check className="h-3 w-3 text-success" />{s}</div>
          ))}
        </div>
        <div className="mt-4 flex gap-1">{[1, 2, 3, 4].map((i) => <span key={i} className="h-1 flex-1 rounded-full bg-gradient-accent" style={{ opacity: 1 - i * 0.18 }} />)}</div>
      </div>

      {posts.map((p, i) => (
        <div key={p.p} className={`animate-rise absolute w-[42%] rounded-lg border border-border bg-card p-3 shadow-card ${p.x}`} style={{ ["--d" as string]: `${400 + i * 140}ms` }}>
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-ink">{p.p}</span>
            <span className="rounded bg-accent px-1.5 py-0.5 font-mono text-[9px] text-accent-foreground">v1 · draft</span>
          </div>
          <p className="mt-1.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">{p.t}</p>
        </div>
      ))}

      <div className="animate-rise absolute bottom-[2%] right-[2%] w-[52%] overflow-hidden rounded-xl border border-border bg-card shadow-lift" style={{ ["--d" as string]: "900ms" }}>
        <div className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2">
          <Send className="h-3.5 w-3.5 text-primary" /><span className="text-[11px] font-semibold text-ink">Approval Bot</span>
          <span className="ml-auto font-mono text-[9px] text-muted-foreground">Telegram</span>
        </div>
        <div className="p-3">
          <p className="text-[11px] leading-snug text-ink">3 posts ready for <b>@grid_insights</b>. Each token is bound to its exact version.</p>
          <div className="mt-2.5 grid grid-cols-2 gap-1.5">
            <span className="rounded-md bg-primary py-1.5 text-center text-[11px] font-medium text-primary-foreground">Approve</span>
            <span className="rounded-md border border-border py-1.5 text-center text-[11px] text-muted-foreground">Reject</span>
          </div>
        </div>
      </div>

      <div className="animate-float absolute left-[4%] top-[4%] flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 font-mono text-[10px] text-ink shadow-card">
        <span className="relative flex h-2 w-2"><span className="absolute inset-0 rounded-full bg-primary animate-pulse-ring" /><span className="relative h-2 w-2 rounded-full bg-primary" /></span>
        Feed monitor · 42 items
      </div>
      <div className="animate-float absolute bottom-[26%] left-[6%] hidden sm:flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 font-mono text-[10px] text-ink shadow-card" style={{ animationDelay: "-3s" }}>
        <ShieldCheck className="h-3 w-3 text-success" /> No timeout-to-publish
      </div>
    </div>
  );
}

