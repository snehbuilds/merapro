"use client";

import { useState, useTransition } from "react";

function computeDemoHash(text: string, targetId: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  const str = `${targetId}::${text.trim()}`;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507);
  h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507);
  h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  const p1 = (h1 >>> 0).toString(16).padStart(8, "0");
  const p2 = (h2 >>> 0).toString(16).padStart(8, "0");
  return `sha256:${p1}${p2}c49e7b1a`;
}

const ORIGINAL_TEXT =
  "Postgres 18 native vector indexing + async I/O benchmarks just dropped. Simplifying the stack beats adding another DB.";
const TARGET_ACCOUNT_ID = "acc_x_merahq_99";
const ORIGINAL_HASH = computeDemoHash(ORIGINAL_TEXT, TARGET_ACCOUNT_ID);

export function ApprovalIntegritySection() {
  const [text, setText] = useState<string>(ORIGINAL_TEXT);
  const [, startTransition] = useTransition();

  const currentHash = computeDemoHash(text, TARGET_ACCOUNT_ID);
  const isValid = currentHash === ORIGINAL_HASH;

  const handleReset = () => {
    startTransition(() => {
      setText(ORIGINAL_TEXT);
    });
  };

  return (
    <section id="integrity" className="relative overflow-hidden py-20 sm:py-28 px-4 sm:px-6 lg:px-8 bg-[#060709] border-b border-white/[0.08]">
      {/* Background glow */}
      <div
        className="absolute bottom-10 right-1/4 w-[500px] h-[300px] bg-teal-500/10 blur-[130px] pointer-events-none rounded-full"
        aria-hidden="true"
      />

      <div className="relative mx-auto max-w-7xl">
        {/* Section Heading */}
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-teal-500/30 bg-teal-500/10 px-3 py-1 text-2xs font-mono text-teal-400">
            <span>CRYPTOGRAPHIC INVARIANT</span>
          </div>
          <h2 className="mt-4 font-display text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight">
            Approval integrity is the core invariant.
          </h2>
          <p className="mt-3 text-base text-zinc-400 leading-relaxed">
            Most social schedulers either post blindly or feature arbitrary timeout-to-publish defaults.
            Mera enforces an immutable cryptographic lock: nothing publishes without a signed approval bound
            to an exact content version and target account. Edit a single byte and the approval is dead.
          </p>
        </div>

        {/* Interactive Cyber-Terminal Workbench */}
        <div className="mt-10 rounded-2xl border border-white/10 bg-[#0c0d12]/95 p-6 sm:p-8 backdrop-blur-xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.08] pb-4">
            <div>
              <span className="font-mono text-xs font-semibold tracking-wider text-emerald-400 uppercase">
                Interactive Hash Sandbox
              </span>
              <p className="text-2xs text-zinc-400 mt-0.5">
                Type or delete any character below to observe instant approval revocation.
              </p>
            </div>
            {isValid ? (
              <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/40 bg-emerald-500/15 px-3.5 py-1 font-mono text-2xs font-semibold text-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.35)]">
                <span className="size-2 rounded-full bg-emerald-400 animate-pulse" />
                <span>APPROVAL TOKEN ACTIVE</span>
              </div>
            ) : (
              <div className="inline-flex items-center gap-2 rounded-full border border-rose-500/40 bg-rose-500/15 px-3.5 py-1 font-mono text-2xs font-semibold text-rose-400 shadow-[0_0_15px_rgba(244,63,94,0.35)]">
                <span className="size-2 rounded-full bg-rose-400 animate-ping" />
                <span>APPROVAL REVOKED — BYTE MISMATCH</span>
              </div>
            )}
          </div>

          <div className="mt-6 grid gap-6 lg:grid-cols-12">
            {/* Editor Area (7 cols) */}
            <div className="lg:col-span-7 flex flex-col">
              <label htmlFor="hash-inspector-input" className="block text-xs font-medium text-zinc-300 mb-2">
                Draft Variant Payload (<span className="font-mono text-emerald-400">{TARGET_ACCOUNT_ID}</span>):
              </label>
              <textarea
                id="hash-inspector-input"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={4}
                className={`w-full rounded-xl border p-4 font-mono text-xs text-white leading-relaxed resize-none transition-all outline-hidden ${
                  isValid
                    ? "border-white/10 bg-black/40 focus:border-emerald-500/50"
                    : "border-rose-500/50 bg-rose-950/20 shadow-[0_0_15px_rgba(244,63,94,0.15)]"
                }`}
              />
              <div className="mt-2.5 flex items-center justify-between text-2xs text-zinc-400">
                <span>{text.length} characters</span>
                {!isValid && (
                  <button
                    onClick={handleReset}
                    className="font-medium text-emerald-400 hover:text-emerald-300 hover:underline"
                  >
                    Restore original signed draft
                  </button>
                )}
              </div>
            </div>

            {/* Cryptographic Inspector Output (5 cols) */}
            <div className="lg:col-span-5 rounded-xl border border-white/[0.08] bg-black/50 p-5 font-mono text-xs flex flex-col justify-between">
              <div className="space-y-3.5">
                <div>
                  <div className="text-2xs text-zinc-400 uppercase tracking-wider">Target Account Boundary</div>
                  <div className="font-semibold text-white mt-0.5">{TARGET_ACCOUNT_ID}</div>
                </div>

                <div>
                  <div className="text-2xs text-zinc-400 uppercase tracking-wider">Calculated Composite Hash</div>
                  <div className={`mt-0.5 break-all font-bold ${isValid ? "text-emerald-400" : "text-rose-400"}`}>
                    {currentHash}
                  </div>
                </div>

                <div>
                  <div className="text-2xs text-zinc-400 uppercase tracking-wider">Signed Token State</div>
                  <div className="mt-0.5 text-2xs text-zinc-300">
                    {isValid ? (
                      <span className="text-emerald-300">
                        Token <code className="font-bold text-white">tok_sign_92a8...</code> matches registered state. Publish authorized.
                      </span>
                    ) : (
                      <span className="text-rose-400">
                        Hash differs from token signature. Egress blocked by deterministic adapter.
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <div className="mt-4 pt-3.5 border-t border-white/[0.08] text-2xs font-sans text-zinc-400 leading-tight">
                No timeout-to-publish bypass exists. A version approved for one account is structurally incapable of publishing to another.
              </div>
            </div>
          </div>
        </div>

        {/* 3 Core Structural Guarantees Bento */}
        <div className="mt-8 grid gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-white/10 bg-[#0c0d12]/90 p-5 backdrop-blur-md">
            <div className="font-mono text-2xs font-semibold text-emerald-400">01 &bull; IMMUTABLE VERSIONS</div>
            <p className="mt-2 text-xs text-zinc-400 leading-relaxed">
              Every draft revision writes a new immutable version record. History is never overwritten, guaranteeing a complete audit ledger.
            </p>
          </div>
          <div className="rounded-xl border border-white/10 bg-[#0c0d12]/90 p-5 backdrop-blur-md">
            <div className="font-mono text-2xs font-semibold text-emerald-400">02 &bull; ZERO TIMEOUT-TO-PUBLISH</div>
            <p className="mt-2 text-xs text-zinc-400 leading-relaxed">
              When an approval window expires, the request terminates immediately. The system never publishes unconfirmed drafts.
            </p>
          </div>
          <div className="rounded-xl border border-white/10 bg-[#0c0d12]/90 p-5 backdrop-blur-md">
            <div className="font-mono text-2xs font-semibold text-emerald-400">03 &bull; NO CROSS-PLATFORM DRIFT</div>
            <p className="mt-2 text-xs text-zinc-400 leading-relaxed">
              Signatures include the exact social account UUID. An approval for LinkedIn can never be re-used to publish to X or Threads.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
