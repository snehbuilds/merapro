"use client";

import { useState } from "react";
import { PlatformMark } from "@/components/icons";
import { TelegramSimulator } from "./telegram-simulator";

interface Variant {
  id: string;
  platform: "linkedin" | "x" | "bluesky";
  accountLabel: string;
  handle: string;
  voice: string;
  charCount: number;
  maxChars: number;
  content: string;
  hashtags: string[];
}

const DEMO_VARIANTS: Variant[] = [
  {
    id: "var_linkedin",
    platform: "linkedin",
    accountLabel: "LinkedIn Personal",
    handle: "Snehashis · Founder",
    voice: "Executive, architectural, high-signal",
    charCount: 642,
    maxChars: 3000,
    content: `PostgreSQL 18 just landed with native vector indexing and asynchronous I/O built directly into the core engine.\n\nFor teams managing dedicated vector databases alongside their primary relational store, this is a consolidation milestone. By moving vector search into Postgres itself, you eliminate data duplication pipelines, simplify point-in-time recovery, and cut infrastructure overhead.\n\nOur benchmark tests show query latency drops by 32% under concurrent read load compared to external synchronization services. Architecture simplicity wins again.`,
    hashtags: ["#PostgreSQL", "#DataArchitecture", "#EngineeringLeadership"],
  },
  {
    id: "var_x",
    platform: "x",
    accountLabel: "X Technical",
    handle: "@merahq",
    voice: "Compact, benchmark-driven, direct",
    charCount: 224,
    maxChars: 280,
    content: `Postgres 18 native vector indexing + async I/O benchmarks just dropped:\n\n• Zero external sync drift\n• 32% latency reduction under concurrent load\n• Transactional consistency for vector embeddings\n\nSimplifying the stack beats adding another DB.`,
    hashtags: [],
  },
  {
    id: "var_bluesky",
    platform: "bluesky",
    accountLabel: "Bluesky Open Source",
    handle: "@mera.dev",
    voice: "Self-hosting ethos, decentralized",
    charCount: 278,
    maxChars: 300,
    content: `Massive win for open source infrastructure: Postgres 18 integrates native vector indexing directly into core.\n\nNo specialized proprietary vector SaaS required. You can run performant semantic search completely self-hosted on standard hardware.`,
    hashtags: ["#Postgres", "#SelfHosted"],
  },
];

export function DispatchLedgerShowcase() {
  const [selectedVariantId, setSelectedVariantId] = useState<string>("var_linkedin");

  const fallbackVariant: Variant = DEMO_VARIANTS[0]!;
  const activeVariant =
    DEMO_VARIANTS.find((v) => v.id === selectedVariantId) ?? fallbackVariant;

  return (
    <section id="post-sets" className="relative overflow-hidden py-20 sm:py-28 px-4 sm:px-6 lg:px-8 bg-[#07080a] border-b border-white/[0.08]">
      {/* Ambient background bloom */}
      <div
        className="absolute top-1/4 left-1/3 -translate-x-1/2 w-[600px] h-[350px] bg-emerald-500/10 blur-[130px] pointer-events-none rounded-full"
        aria-hidden="true"
      />

      <div className="relative mx-auto max-w-7xl">
        {/* Section Header */}
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-2xs font-mono text-emerald-400">
            <span>PER-ACCOUNT AUTHORSHIP</span>
          </div>
          <h2 className="mt-4 font-display text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight">
            One story. A unique post for every target account.
          </h2>
          <p className="mt-3 text-base text-zinc-400 leading-relaxed">
            Mera never blindly cross-posts identical copy. A single news trigger produces a{" "}
            <span className="text-white font-medium">Post Set</span>—with distinct wording, tailored
            rhythm, and dedicated voice profiles per account. This eliminates platform duplicate suspensions.
          </p>
        </div>

        {/* Source Article Ingest Banner */}
        <div className="mt-8 rounded-xl border border-white/10 bg-[#0c0d12]/90 p-4 backdrop-blur-md shadow-[0_4px_20px_rgba(0,0,0,0.5)]">
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2.5">
              <span className="rounded-full bg-emerald-500/20 border border-emerald-500/40 px-2 py-0.5 font-mono text-2xs font-semibold text-emerald-400 uppercase">
                TRIGGER SOURCE
              </span>
              <span className="text-zinc-500">&bull;</span>
              <span className="font-medium text-white">Hacker News / PostgreSQL Weekly</span>
            </div>
            <div className="flex items-center gap-4 font-mono text-2xs text-zinc-400">
              <span>RELEVANCE: <strong className="text-emerald-400">0.94</strong></span>
              <span>GROUP: <strong className="text-white">Tech News</strong></span>
              <span>TOPIC: <strong className="text-white">Database Infra</strong></span>
            </div>
          </div>
          <div className="mt-2 text-sm font-semibold text-white">
            &ldquo;PostgreSQL 18 Released with Native Vector Search and Async I/O Engine&rdquo;
          </div>
        </div>

        {/* 21st.dev Style Bento Grid */}
        <div className="mt-6 grid gap-6 lg:grid-cols-12">
          {/* Left Column: Post Set Variant Studio (7 cols) */}
          <div className="lg:col-span-7 flex flex-col rounded-2xl border border-white/10 bg-[#0c0d12]/90 backdrop-blur-xl overflow-hidden shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
            {/* Account Selector Tabs */}
            <div className="flex border-b border-white/[0.08] bg-white/[0.02] overflow-x-auto p-1.5 gap-1.5">
              {DEMO_VARIANTS.map((variant) => {
                const isActive = variant.id === selectedVariantId;
                return (
                  <button
                    key={variant.id}
                    onClick={() => setSelectedVariantId(variant.id)}
                    className={`flex items-center gap-2 rounded-lg px-3.5 py-2 text-xs font-medium transition-all whitespace-nowrap ${
                      isActive
                        ? "bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 shadow-[0_0_15px_rgba(16,185,129,0.2)]"
                        : "text-zinc-400 hover:text-white hover:bg-white/[0.04]"
                    }`}
                  >
                    <PlatformMark platform={variant.platform} size="sm" />
                    <span>{variant.accountLabel}</span>
                  </button>
                );
              })}
            </div>

            {/* Active Variant Content Card */}
            <div className="p-6 flex-1 flex flex-col justify-between">
              <div>
                {/* Account Details Bar */}
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.08] pb-3 text-xs">
                  <div>
                    <span className="font-semibold text-white text-sm">{activeVariant.handle}</span>
                    <span className="block text-2xs text-zinc-400 mt-0.5">
                      Voice profile: <span className="text-zinc-300">{activeVariant.voice}</span>
                    </span>
                  </div>
                  <div className="font-mono text-2xs text-zinc-400 bg-white/[0.04] px-2.5 py-1 rounded-md border border-white/[0.08]">
                    {activeVariant.charCount} / {activeVariant.maxChars} chars
                  </div>
                </div>

                {/* Post Body Preview */}
                <div className="mt-4 whitespace-pre-wrap font-sans text-sm text-zinc-200 leading-relaxed">
                  {activeVariant.content}
                </div>

                {/* Hashtags */}
                {activeVariant.hashtags.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-2 pt-3 border-t border-white/[0.08]">
                    {activeVariant.hashtags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-0.5 font-mono text-2xs font-medium text-emerald-400"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Guardrail Status Footer */}
              <div className="mt-6 pt-4 border-t border-white/[0.08] flex flex-wrap items-center justify-between gap-3 font-mono text-2xs">
                <div className="flex items-center gap-2 text-emerald-400">
                  <span className="size-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
                  <span>CROSS-ACCOUNT DUPLICATE CHECK: 18% SIMILARITY (PASS)</span>
                </div>
                <div className="text-zinc-400">
                  STATUS: <span className="text-white font-medium">READY FOR OPERATOR SIGN-OFF</span>
                </div>
              </div>
            </div>
          </div>

          {/* Right Column: Telegram Approval Simulator (5 cols) */}
          <div id="approval-bot" className="lg:col-span-5 flex flex-col justify-between">
            <TelegramSimulator />
          </div>
        </div>
      </div>
    </section>
  );
}
