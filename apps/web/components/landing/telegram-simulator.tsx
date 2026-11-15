"use client";

import { useState } from "react";

type ApprovalState = "idle" | "approved" | "revision" | "rejected";

export function TelegramSimulator() {
  const [state, setState] = useState<ApprovalState>("idle");
  const [copiedHash, setCopiedHash] = useState(false);

  const hash = "sha256:7b2f481c90df035e";

  const handleCopyHash = () => {
    navigator.clipboard?.writeText(hash);
    setCopiedHash(true);
    setTimeout(() => setCopiedHash(false), 2000);
  };

  return (
    <div className="relative rounded-2xl border border-white/10 bg-[#0c0d12]/90 p-5 backdrop-blur-xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
      {/* Telegram Channel Header */}
      <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
        <div className="flex items-center gap-3">
          <div className="relative flex size-9 items-center justify-center rounded-full bg-gradient-to-tr from-emerald-500/30 to-teal-400/20 border border-emerald-400/40 text-emerald-300 font-mono text-xs font-bold shadow-[0_0_15px_rgba(16,185,129,0.3)]">
            M
            <span className="absolute bottom-0 right-0 size-2.5 rounded-full bg-emerald-400 border-2 border-[#0c0d12]" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-semibold text-white">Mera Dispatch Bot</span>
              <span className="rounded-full bg-emerald-500/20 border border-emerald-500/40 px-1.5 py-0.2 font-mono text-[9px] font-semibold text-emerald-400 uppercase">
                BOT
              </span>
            </div>
            <span className="text-[11px] text-zinc-400">Telegram Bot Gateway &bull; Verified</span>
          </div>
        </div>
        <span className="font-mono text-2xs text-zinc-500">Today, 08:42</span>
      </div>

      {/* Simulated Message Bubble */}
      <div className="mt-4 rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 text-xs font-sans">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="font-semibold text-white text-sm">
              Approval Request <span className="font-mono text-emerald-400">#REQ-8F10B2</span>
            </div>
            <div className="text-2xs text-zinc-400 mt-0.5">
              Source: <span className="text-zinc-300 italic">PostgreSQL 18 Release Notes & Benchmarks</span>
            </div>
          </div>
          {state === "idle" && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-0.5 font-mono text-2xs font-semibold text-amber-400 shadow-[0_0_10px_rgba(245,158,11,0.2)]">
              <span className="size-1.5 rounded-full bg-amber-400 animate-pulse" />
              09:48 REMAINING
            </span>
          )}
          {state === "approved" && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/40 bg-emerald-500/15 px-2.5 py-0.5 font-mono text-2xs font-semibold text-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.35)]">
              ✓ SIGNED &amp; ENQUEUED
            </span>
          )}
          {state === "revision" && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-500/40 bg-cyan-500/15 px-2.5 py-0.5 font-mono text-2xs font-semibold text-cyan-400 shadow-[0_0_12px_rgba(6,182,212,0.35)]">
              ✎ REVISION SENT
            </span>
          )}
          {state === "rejected" && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-500/40 bg-rose-500/15 px-2.5 py-0.5 font-mono text-2xs font-semibold text-rose-400 shadow-[0_0_12px_rgba(244,63,94,0.35)]">
              ✕ TERMINAL REJECT
            </span>
          )}
        </div>

        {/* Post Set Preview Lines */}
        <div className="mt-3.5 space-y-2 border-y border-white/[0.08] py-2.5 text-2xs">
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Target Group:</span>
            <span className="font-mono font-medium text-white">Tech News (3 accounts)</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Draft Variants:</span>
            <span className="font-mono text-emerald-400">3 distinct versions (18% sim.)</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-zinc-400">Composite Hash:</span>
            <button
              onClick={handleCopyHash}
              className="font-mono text-emerald-400 hover:text-emerald-300 hover:underline inline-flex items-center gap-1.5"
              title="Click to copy hash"
            >
              <span>{hash}</span>
              <span className="text-[10px] text-zinc-500">
                {copiedHash ? "(copied!)" : "(copy)"}
              </span>
            </button>
          </div>
        </div>

        {/* State Banner with Glowing Alert */}
        {state === "approved" && (
          <div className="mt-3.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-2xs text-emerald-300 shadow-[0_0_15px_rgba(16,185,129,0.2)]">
            <strong className="text-emerald-200">Approval Token Minted:</strong> Single-use token{" "}
            <code className="font-mono font-bold text-white">tok_9a12c8...</code> issued.
            Variants enqueued for staggered release (+0m, +4m, +9m).
          </div>
        )}
        {state === "revision" && (
          <div className="mt-3.5 rounded-lg border border-cyan-500/40 bg-cyan-500/10 p-3 text-2xs text-cyan-300 shadow-[0_0_15px_rgba(6,182,212,0.2)]">
            <strong className="text-cyan-200">Revision Triggered:</strong> Instructions dispatched to Writer agent.
            Current approval token invalidated.
          </div>
        )}
        {state === "rejected" && (
          <div className="mt-3.5 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-2xs text-rose-300 shadow-[0_0_15px_rgba(244,63,94,0.2)]">
            <strong className="text-rose-200">Set Terminated:</strong> Nothing will be published to any target account.
            Event logged to audit trail.
          </div>
        )}

        {/* Interactive Telegram Buttons */}
        <div className="mt-4 grid grid-cols-3 gap-2">
          <button
            type="button"
            className={`h-9 rounded-lg font-semibold text-xs transition-all ${
              state === "approved"
                ? "bg-emerald-500 text-black shadow-[0_0_20px_rgba(16,185,129,0.5)]"
                : "border border-emerald-500/40 bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25 hover:shadow-[0_0_15px_rgba(16,185,129,0.3)]"
            }`}
            onClick={() => setState("approved")}
          >
            ✓ Approve
          </button>
          <button
            type="button"
            className={`h-9 rounded-lg font-medium text-xs transition-all ${
              state === "revision"
                ? "bg-cyan-500 text-black shadow-[0_0_20px_rgba(6,182,212,0.5)]"
                : "border border-white/10 bg-white/[0.04] text-zinc-300 hover:bg-white/[0.08] hover:text-white"
            }`}
            onClick={() => setState("revision")}
          >
            ✎ Reword
          </button>
          <button
            type="button"
            className={`h-9 rounded-lg font-medium text-xs transition-all ${
              state === "rejected"
                ? "bg-rose-500 text-white shadow-[0_0_20px_rgba(244,63,94,0.5)]"
                : "border border-rose-500/30 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20"
            }`}
            onClick={() => setState("rejected")}
          >
            ✕ Reject
          </button>
        </div>

        {state !== "idle" && (
          <div className="mt-3 text-right">
            <button
              onClick={() => setState("idle")}
              className="font-mono text-2xs text-zinc-400 hover:text-white underline"
            >
              Reset simulator state
            </button>
          </div>
        )}
      </div>

      <div className="mt-3 text-2xs text-zinc-500 text-center">
        A single tap generates an immutable cryptographic signature scoped to exact destination accounts.
      </div>
    </div>
  );
}
