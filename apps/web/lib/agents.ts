import type { AgentRun, AgentStep, CostRow } from "@/lib/api/models";
import type { StatusShape, StatusTone } from "@/lib/status";

/*
 * Agents view logic (agents.html): money, run and step states, step
 * descriptions and the guardrail thresholds. Costs arrive as decimal
 * strings; a sandbox run really costs $0 and is shown as such.
 */

/**
 * "$0.1623", "$18.40", "$0". Two decimals from a dollar up, four below, and
 * no trailing noise beyond that. Never rounds a real cost down to "$0".
 */
export function formatUsd(value: string | number): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "$0";
  if (Math.abs(n) >= 1) return `$${n.toFixed(2)}`;
  if (Math.abs(n) < 0.0001) return "<$0.0001";
  return `$${n.toFixed(4)}`;
}

export function costValue(value: string): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

/** 38123 → "38.1s", 2100 → "2.1s", 812 → "812ms", 125000 → "2m 5s". */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

/** Wall time of a finished run; null while it is still going. */
export function runDuration(run: Pick<AgentRun, "started_at" | "finished_at">): number | null {
  if (!run.finished_at) return null;
  return new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
}

export function formatTokens(n: number): string {
  return n.toLocaleString("en-US");
}

const st = (label: string, tone: StatusTone, shape: StatusShape) => ({ label, tone, shape });

/** agent_run.state (queued, running, awaiting_human, succeeded, failed, aborted). */
export const RUN_STATE: Readonly<Record<string, { label: string; tone: StatusTone; shape: StatusShape }>> = {
  queued: st("Queued", "info", "ring"),
  running: st("Running", "info", "half"),
  awaiting_human: st("Paused", "warn", "half"),
  succeeded: st("Succeeded", "ok", "full"),
  failed: st("Failed", "danger", "full"),
  aborted: st("Aborted", "neutral", "ring"),
};

export function runState(state: string) {
  return RUN_STATE[state] ?? st(state.replace(/_/g, " "), "neutral", "ring");
}

/** Step rows are tinted for fallbacks and failures (agents.html step 7). */
export function stepTone(step: Pick<AgentStep, "state" | "fallback_from" | "error" | "output" | "step_type">): "warn" | "ok" | "danger" | undefined {
  if (step.state === "failed" || step.error) return "danger";
  if (step.fallback_from) return "warn";
  if (step.step_type === "guardrail") {
    const outcome = (step.output as Record<string, unknown>).outcome;
    if (outcome === "block") return "danger";
    if (outcome === "warn") return "warn";
    if (outcome === "pass") return "ok";
  }
  return undefined;
}

export const STEP_TYPE_LABEL: Readonly<Record<string, string>> = {
  llm_call: "model call",
  embedding: "embedding",
  tool_call: "tool call",
  guardrail: "guardrail",
  handoff: "handoff",
  human_checkpoint: "human checkpoint",
};

export function stepTypeLabel(type: string): string {
  return STEP_TYPE_LABEL[type] ?? type.replace(/_/g, " ");
}

/** The workflow key in words for the runs list ("draft_from_content" stays mono, this is the sub line). */
export function workflowLabel(key: string): string {
  return key.replace(/_/g, " ");
}

/** Rows sorted by spend, most first; zero-cost rows kept (sandbox runs are real). */
export function byCost(rows: readonly CostRow[]): CostRow[] {
  return [...rows].sort((a, b) => costValue(b.cost_usd) - costValue(a.cost_usd));
}

/**
 * The thresholds the guardrail layer applies (backend drafting.core and
 * domain policies). Static: they are code, not settings, in Phase 1.
 */
export const GUARDRAIL_THRESHOLDS: readonly { label: string; value: string; note?: string }[] = [
  { label: "Cross-account duplicate", value: "block ≥ 0.90 · warn ≥ 0.80", note: "Two accounts in one set, same platform" },
  { label: "Same-account duplicate", value: "block ≥ 0.95 · warn ≥ 0.85", note: "Against the account's recent posts" },
  { label: "Banned words", value: "block on any", note: "From the account's resolved voice" },
  { label: "Personal data", value: "block on any", note: "Emails, phone numbers and the like" },
  { label: "Platform limits", value: "block over limit", note: "Length, thread support; too many hashtags warns" },
];

export const GUARDRAILS_LATER: readonly string[] = ["Toxicity", "Blocked topics", "Daily cap per account"];

export const RUNS_LIMIT = 20;

export const AGENT_ERROR_TEXT: Readonly<Record<string, string>> = {
  forbidden: "You don't have permission to see agent runs here.",
  not_found: "That run no longer exists. Reload to see the current list.",
};
