"use client";

import { useState } from "react";
import { Search, PenLine, Hash, ClipboardCheck, ImageIcon, MessageSquareCheck, CalendarClock, Send, MessagesSquare, BarChart3, Workflow, type LucideIcon } from "./icons";
import { Reveal, SectionHeading } from "./Reveal";
import { cn } from "@/lib/utils";

type Agent = { name: string; icon: LucideIcon; stage: string; does: string; can: string; cannot: string; llm: boolean; note?: string };
const agents: Agent[] = [
  { name: "Research", icon: Search, stage: "Discover", llm: true, does: "Monitors industry feeds, gathers sources and produces a cited research brief for each story.", can: "Read feeds and sources", cannot: "Write or publish posts" },
  { name: "Writer", icon: PenLine, stage: "Create", llm: true, does: "Drafts a distinct, platform-native post set for every target account from a single brief.", can: "Create drafts per account", cannot: "Approve or schedule" },
  { name: "Hashtag", icon: Hash, stage: "Create", llm: true, does: "Suggests tags suited to each platform and account, rather than one list for all.", can: "Annotate drafts", cannot: "Change post body" },
  { name: "Editor & Compliance", icon: ClipboardCheck, stage: "Review", llm: true, does: "Checks tone, accuracy against the brief and workspace policy before anything reaches a human.", can: "Revise or block drafts", cannot: "Approve on a human's behalf" },
  { name: "Media", icon: ImageIcon, stage: "Create", llm: true, does: "Prepares media assets sized for each destination platform.", can: "Attach media to drafts", cannot: "Publish" },
  { name: "Approval Bot", icon: MessageSquareCheck, stage: "Approve", llm: false, does: "Sends one-tap approval requests over Telegram or WhatsApp and records signed decisions.", can: "Issue version-bound tokens", cannot: "Approve itself or extend expiry" },
  { name: "Scheduler", icon: CalendarClock, stage: "Distribute", llm: false, does: "Staggers approved posts across accounts with adaptive jitter.", can: "Queue approved jobs", cannot: "Queue unapproved content" },
  { name: "Publisher", icon: Send, stage: "Distribute", llm: false, note: "Zero LLM", does: "Deterministic: verifies the approval token against content and account, then calls the official platform adapter.", can: "Call official APIs", cannot: "Generate or alter content" },
  { name: "Engagement", icon: MessagesSquare, stage: "Learn", llm: true, does: "Watches responses on published posts and surfaces what needs attention.", can: "Read engagement", cannot: "Post replies unapproved" },
  { name: "Analytics & Feedback", icon: BarChart3, stage: "Learn", llm: true, does: "Turns performance into feedback that informs future research and drafting.", can: "Read metrics", cannot: "Modify live posts" },
  { name: "Orchestration", icon: Workflow, stage: "Coordinate", llm: false, note: "Coordination slot", does: "Coordinates durable job handoffs between agents. Shown here as a coordination role; the published materials name ten roles explicitly.", can: "Route typed jobs", cannot: "Bypass approval" },
];

export function Agents() {
  const [sel, setSel] = useState(7);
  const a = agents[sel] ?? agents[0]!;
  return (
    <section id="architecture" className="mx-auto max-w-7xl px-5 py-24 sm:px-8 lg:py-32">
      <SectionHeading eyebrow="Architecture" title={<>Eleven typed agents. <span className="text-primary">Hard boundaries.</span></>}
        body="Each agent has a declared capability scope and passes work through durable, typed job handoffs. Language models help write; they never hold the keys to publish." />
      <div className="mt-14 grid gap-6 lg:grid-cols-[1fr_380px]">
        <Reveal className="relative rounded-xl border border-border bg-card p-4 sm:p-6">
          <div className="pointer-events-none absolute inset-0 rounded-xl bg-grid opacity-60" aria-hidden />
          <ol className="relative grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4" aria-label="Agent pipeline">
            {agents.map((ag, i) => (
              <li key={ag.name}>
                <button onClick={() => setSel(i)} aria-pressed={i === sel}
                  className={cn("group flex h-full w-full flex-col gap-3 rounded-lg border p-3 text-left transition-all", i === sel ? "border-primary bg-accent shadow-card" : "border-border bg-card hover:-translate-y-0.5 hover:border-primary/40")}>
                  <div className="flex items-center justify-between">
                    <ag.icon className={cn("h-4 w-4", i === sel ? "text-primary" : "text-muted-foreground group-hover:text-primary")} strokeWidth={1.7} />
                    <span className="font-mono text-[10px] text-muted-foreground">{String(i + 1).padStart(2, "0")}</span>
                  </div>
                  <div>
                    <p className="text-sm font-semibold leading-tight text-ink">{ag.name}</p>
                    <p className="mt-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{ag.stage}{!ag.llm && " · no LLM"}</p>
                  </div>
                </button>
              </li>
            ))}
          </ol>
        </Reveal>
        <Reveal delay={120} className="rounded-xl border border-border bg-surface p-6" >
          <div key={a.name} className="animate-swap" aria-live="polite">
            <div className="flex items-center gap-3">
              <span className="grid h-11 w-11 place-items-center rounded-lg bg-primary text-primary-foreground"><a.icon className="h-5 w-5" /></span>
              <div><p className="font-display text-3xl leading-none text-ink">{a.name}</p>
                <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{a.stage}{a.note && ` · ${a.note}`}</p></div>
            </div>
            <p className="mt-5 text-[15px] leading-relaxed text-ink">{a.does}</p>
            <dl className="mt-6 space-y-3 border-t border-border pt-5 text-sm">
              <div className="flex justify-between gap-4"><dt className="text-muted-foreground">Capability</dt><dd className="text-right text-ink">{a.can}</dd></div>
              <div className="flex justify-between gap-4"><dt className="text-muted-foreground">Boundary</dt><dd className="text-right text-ink">{a.cannot}</dd></div>
              <div className="flex justify-between gap-4"><dt className="text-muted-foreground">Uses a model</dt><dd className="text-ink">{a.llm ? "Yes" : "No — deterministic"}</dd></div>
            </dl>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

