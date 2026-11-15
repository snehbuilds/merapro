"use client";

import { PlatformMark } from "@/components/icons";

interface StaggerStep {
  timeOffset: string;
  platform: "linkedin" | "x" | "threads" | "bluesky";
  accountName: string;
  handle: string;
  status: "published" | "scheduled";
  delayNote: string;
}

const STAGGER_DATA: StaggerStep[] = [
  {
    timeOffset: "09:00 (T+00m)",
    platform: "linkedin",
    accountName: "LinkedIn Personal",
    handle: "Snehashis · Founder",
    status: "published",
    delayNote: "Immediate release (Slot 1 anchor)",
  },
  {
    timeOffset: "09:04 (T+04m)",
    platform: "x",
    accountName: "X Technical",
    handle: "@merahq",
    status: "published",
    delayNote: "+4m automated jitter delay",
  },
  {
    timeOffset: "09:09 (T+09m)",
    platform: "threads",
    accountName: "Threads Brand",
    handle: "@mera.dispatch",
    status: "scheduled",
    delayNote: "+5m queue offset",
  },
  {
    timeOffset: "09:14 (T+14m)",
    platform: "x",
    accountName: "X Personal",
    handle: "@snehashis",
    status: "scheduled",
    delayNote: "+5m stagger (avoids duplicate heuristic)",
  },
  {
    timeOffset: "09:19 (T+19m)",
    platform: "bluesky",
    accountName: "Bluesky Open Source",
    handle: "@mera.dev",
    status: "scheduled",
    delayNote: "+5m final group fan-out",
  },
];

export function StaggerTimelineSection() {
  return (
    <section id="stagger" className="relative overflow-hidden py-20 sm:py-28 px-4 sm:px-6 lg:px-8 bg-[#060709] border-b border-white/[0.08]">
      {/* Background glow */}
      <div
        className="absolute top-1/3 right-1/4 w-[500px] h-[300px] bg-cyan-500/10 blur-[130px] pointer-events-none rounded-full"
        aria-hidden="true"
      />

      <div className="relative mx-auto max-w-7xl">
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-2xs font-mono text-cyan-400">
            <span>MULTI-CHANNEL STAGGER</span>
          </div>
          <h2 className="mt-4 font-display text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight">
            Staggered multi-channel distribution.
          </h2>
          <p className="mt-3 text-base text-zinc-400 leading-relaxed">
            Blasting five accounts simultaneously is the fastest way to get flagged by platform spam heuristics.
            Mera manages <span className="text-white font-medium">Account Groups</span> across platforms,
            intelligently staggering publications with adaptive jitter delays to protect your accounts.
          </p>
        </div>

        {/* 21st.dev Style Timeline Card */}
        <div className="mt-10 rounded-2xl border border-white/10 bg-[#0c0d12]/95 p-6 sm:p-8 backdrop-blur-xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.08] pb-4">
            <div>
              <div className="font-semibold text-white text-sm">Account Group: &ldquo;Tech News&rdquo;</div>
              <div className="text-xs text-zinc-400">
                5 Connected Accounts &bull; 15-Minute Adaptive Jitter Window
              </div>
            </div>
            <div className="flex items-center gap-2 font-mono text-xs text-emerald-400">
              <span className="size-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
              <span className="font-medium">QUEUE PROGRESS: 2/5 PUBLISHED</span>
            </div>
          </div>

          {/* Timeline Steps */}
          <div className="mt-6 space-y-3.5">
            {STAGGER_DATA.map((step, idx) => (
              <div
                key={step.handle}
                className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 transition-all hover:bg-white/[0.05] hover:border-white/15"
              >
                <div className="flex items-center gap-3.5">
                  <div className="font-mono text-xs font-bold text-emerald-400 w-6 shrink-0">
                    0{idx + 1}
                  </div>
                  <div className="size-8 rounded-lg border border-white/10 bg-black/40 flex items-center justify-center shrink-0 text-white">
                    <PlatformMark platform={step.platform} size="sm" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-white">{step.accountName}</span>
                      <span className="font-mono text-2xs text-zinc-400">{step.handle}</span>
                    </div>
                    <div className="text-2xs text-zinc-400 mt-0.5">{step.delayNote}</div>
                  </div>
                </div>

                <div className="flex items-center justify-between sm:justify-end gap-4 font-mono text-xs">
                  <span className="text-zinc-400 text-2xs">{step.timeOffset}</span>
                  {step.status === "published" ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/40 bg-emerald-500/15 px-2.5 py-0.5 text-2xs font-semibold text-emerald-400 shadow-[0_0_10px_rgba(16,185,129,0.3)]">
                      ✓ PUBLISHED
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-0.5 text-2xs font-semibold text-zinc-400">
                      ⏱ QUEUED
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-6 pt-4 border-t border-white/[0.08] flex flex-wrap items-center justify-between gap-3 text-xs text-zinc-400">
            <div>
              <strong>Partial outcomes are normal:</strong> If one platform API rate-limits, its retry job is isolated. Sibling accounts continue without blocking the set.
            </div>
            <div className="font-mono text-2xs">
              BACKOFF: <span className="text-white">EXPONENTIAL (max 5 retries)</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
