"use client";

import { useState } from "react";

interface AgentDef {
  id: string;
  name: string;
  runsPer: string;
  input: string;
  output: string;
  sideEffect: string;
  hasLLM: boolean;
  description: string;
}

const AGENTS: AgentDef[] = [
  {
    id: "research",
    name: "Research Agent",
    runsPer: "Content Item",
    input: "Raw fetched articles, RSS feeds, Reddit, YouTube, MCP data",
    output: "Scored, summarized, and topic-classified items per account group",
    sideEffect: "Writes content items to database",
    hasLLM: true,
    description: "Filters incoming feeds against your account group's content pillars and relevance weights.",
  },
  {
    id: "writer",
    name: "Writer Agent",
    runsPer: "Target Account",
    input: "Content item + Account brand voice profile + Character limit",
    output: "One immutable draft version for this specific destination",
    sideEffect: "Writes post versions. Structural inability to publish.",
    hasLLM: true,
    description: "Authors unique copy per account. Two X handles or LinkedIn profiles get entirely distinct phrasing.",
  },
  {
    id: "hashtag",
    name: "Hashtag Agent",
    runsPer: "Target Account",
    input: "Draft text + Platform conventions + Account historical conversion stats",
    output: "Ranked hashtags with declared evidence basis and sample size",
    sideEffect: "Attaches metadata to post version",
    hasLLM: true,
    description: "Replaces guesswork with empirical tag selection proven by your past account metrics.",
  },
  {
    id: "editor",
    name: "Editor & Compliance",
    runsPer: "Account & Set",
    input: "Draft variants + Source article + Cross-account sibling drafts",
    output: "Fact-check flags, confidence scores, duplicate similarity matrix",
    sideEffect: "Can block a variant; siblings continue unblocked",
    hasLLM: true,
    description: "Verifies that claims do not hallucinate past source facts, and ensures cross-account uniqueness.",
  },
  {
    id: "media",
    name: "Media Agent",
    runsPer: "Post Set",
    input: "Post brief + Platform media aspect specs (1:1, 16:9, 9:16)",
    output: "Renditions, crop boundaries, AI accessibility alt-text",
    sideEffect: "Generates or re-encodes assets",
    hasLLM: true,
    description: "Prepares platform-compliant visual assets and compliant WCAG alt-text automatically.",
  },
  {
    id: "approval",
    name: "Approval Bot Agent",
    runsPer: "Approval Request",
    input: "Request payload + Channel constraints (Telegram, WhatsApp)",
    output: "Formatted inline message with single-use signed callbacks",
    sideEffect: "Sends instant notifications to operator phone",
    hasLLM: true,
    description: "Delivers the 10-second decision card to the operator's mobile messaging client.",
  },
  {
    id: "scheduler",
    name: "Scheduler Agent",
    runsPer: "Post Set",
    input: "Approved variants + Group stagger matrix + Target time slots",
    output: "Queued publish jobs with deterministic jitter offsets",
    sideEffect: "Enqueues background jobs",
    hasLLM: false,
    description: "Spreads multiple account posts across a time window to avoid anti-bot flags.",
  },
  {
    id: "publisher",
    name: "Publisher (Zero LLM)",
    runsPer: "Target Account",
    input: "Scheduled variant + Signed unexpired token + Platform credentials",
    output: "Platform API response + Published permalink + Audit row",
    sideEffect: "Publishes to social platforms. NO LLM logic.",
    hasLLM: false,
    description: "Pure deterministic code. Performs assert_publishable and calls official platform adapters.",
  },
  {
    id: "inbox",
    name: "Engagement Agent",
    runsPer: "Inbound Comment",
    input: "Comment + Owning account identity + Sentiment classifier",
    output: "Classification + Draft reply in that specific account's voice",
    sideEffect: "Drafts replies for human sign-off; never rogue replies",
    hasLLM: true,
    description: "Aggregates comments across platforms into one unified inbox with account-matched drafts.",
  },
  {
    id: "analytics",
    name: "Analytics & Feedback",
    runsPer: "Workspace / Group",
    input: "Platform metrics (impressions, clicks, replies, shares)",
    output: "AI insights + Group pillar weight adjustments",
    sideEffect: "Tunes scoring feedback loop",
    hasLLM: true,
    description: "Closes the loop by tuning relevance weights based on real post performance.",
  },
];

export function AgentPipelineSection() {
  const [activeId, setActiveId] = useState<string>("publisher");
  const fallbackAgent: AgentDef = AGENTS[7]!;
  const activeAgent = AGENTS.find((a) => a.id === activeId) ?? fallbackAgent;

  return (
    <section id="pipeline" className="relative overflow-hidden py-20 sm:py-28 px-4 sm:px-6 lg:px-8 bg-[#07080a] border-b border-white/[0.08]">
      {/* Background radial glow */}
      <div
        className="absolute top-1/2 left-1/4 w-[500px] h-[350px] bg-emerald-500/10 blur-[130px] pointer-events-none rounded-full"
        aria-hidden="true"
      />

      <div className="relative mx-auto max-w-7xl">
        {/* Section Heading */}
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-2xs font-mono text-emerald-400">
            <span>TYPED MULTI-AGENT GRAPH</span>
          </div>
          <h2 className="mt-4 font-display text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight">
            Eleven typed agents with hard capability boundaries.
          </h2>
          <p className="mt-3 text-base text-zinc-400 leading-relaxed">
            Every agent in Mera is a strictly typed function with a Pydantic input and output contract.
            Agents never call each other directly; every handoff is a durable database job.
            Most importantly: <strong className="text-white">capability is the security boundary</strong>.
          </p>
        </div>

        {/* 21st.dev Style Horizontal Node Scroll */}
        <div className="mt-8 flex gap-2.5 overflow-x-auto pb-2 scrollbar-none">
          {AGENTS.map((agent, idx) => {
            const isSelected = agent.id === activeId;
            return (
              <button
                key={agent.id}
                onClick={() => setActiveId(agent.id)}
                className={`flex items-center gap-2.5 rounded-xl border px-4 py-2.5 text-xs font-medium transition-all whitespace-nowrap ${
                  isSelected
                    ? "border-emerald-500/50 bg-emerald-500/15 text-white shadow-[0_0_20px_rgba(16,185,129,0.3)]"
                    : "border-white/10 bg-[#0c0d12]/80 text-zinc-400 hover:border-white/20 hover:text-white"
                }`}
              >
                <span className="font-mono text-2xs text-zinc-500">0{idx + 1}</span>
                <span>{agent.name}</span>
                {!agent.hasLLM && (
                  <span className="rounded-full bg-emerald-500/20 border border-emerald-500/40 px-1.5 py-0.2 font-mono text-2xs font-bold text-emerald-400 uppercase">
                    0-LLM
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Selected Agent Detailed Contract Card */}
        <div className="mt-6 rounded-2xl border border-white/10 bg-[#0c0d12]/95 p-6 sm:p-8 backdrop-blur-xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
          <div className="flex flex-wrap items-start justify-between gap-4 border-b border-white/[0.08] pb-5">
            <div>
              <div className="flex items-center gap-3">
                <h3 className="font-display text-xl font-bold text-white">{activeAgent.name}</h3>
                {!activeAgent.hasLLM ? (
                  <span className="rounded-full bg-emerald-500/20 border border-emerald-500/40 px-3 py-0.5 font-mono text-2xs font-bold text-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.3)]">
                    DETERMINISTIC CODE (NO LLM)
                  </span>
                ) : (
                  <span className="rounded-full bg-teal-500/20 border border-teal-500/40 px-3 py-0.5 font-mono text-2xs font-bold text-teal-400">
                    STRUCTURED LLM FUNCTION
                  </span>
                )}
              </div>
              <p className="mt-1.5 text-xs text-zinc-300">{activeAgent.description}</p>
            </div>
            <div className="font-mono text-2xs text-zinc-400 bg-white/[0.04] px-3 py-1.5 rounded-lg border border-white/[0.08]">
              RUNS PER: <strong className="text-white">{activeAgent.runsPer}</strong>
            </div>
          </div>

          <div className="mt-6 grid gap-4 sm:grid-cols-3 font-mono text-xs">
            <div className="rounded-xl border border-white/[0.08] bg-black/40 p-4">
              <span className="text-2xs text-zinc-400 uppercase tracking-wider block mb-1.5">Typed Input Contract</span>
              <p className="font-sans text-xs text-zinc-200 leading-relaxed">{activeAgent.input}</p>
            </div>
            <div className="rounded-xl border border-white/[0.08] bg-black/40 p-4">
              <span className="text-2xs text-zinc-400 uppercase tracking-wider block mb-1.5">Typed Output Contract</span>
              <p className="font-sans text-xs text-zinc-200 leading-relaxed">{activeAgent.output}</p>
            </div>
            <div className="rounded-xl border border-white/[0.08] bg-black/40 p-4">
              <span className="text-2xs text-zinc-400 uppercase tracking-wider block mb-1.5">Side Effect Permission</span>
              <p className="font-sans text-xs text-zinc-200 leading-relaxed">{activeAgent.sideEffect}</p>
            </div>
          </div>

          {activeAgent.id === "publisher" && (
            <div className="mt-5 rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-4 text-xs text-emerald-200 shadow-[0_0_15px_rgba(16,185,129,0.15)]">
              <strong className="text-white font-semibold block mb-1">The Zero-LLM Principle at Egress:</strong>
              The Publisher contains no language model call and has no model configuration. Giving the component that writes to external platform APIs an LLM would undermine every safety guarantee made upstream. It only verifies signatures and executes official HTTP adapters.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
