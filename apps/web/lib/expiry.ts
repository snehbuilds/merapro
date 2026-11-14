/*
 * Approval expiry urgency for post sets and requests. One timer per set
 * (approvals.html "This request"). Thresholds:
 *   > 2h     warn    "Expires in 11h 42m"
 *   <= 2h    danger  "Expires in 1h 54m"  (the mockup's hiring set)
 *   <= 0     neutral "Expired"
 */
import type { StatusShape, StatusTone } from "@/lib/status";

export const EXPIRY_DANGER_MS = 2 * 60 * 60 * 1000;

export type ExpiryUrgency = "warn" | "danger" | "expired";

export function expiryUrgency(remainingMs: number): ExpiryUrgency {
  if (remainingMs <= 0) return "expired";
  if (remainingMs <= EXPIRY_DANGER_MS) return "danger";
  return "warn";
}

/** "11h 42m", "54m", "2d 3h". Minutes are zero-padded after hours, as in the mockups. */
export function formatRemaining(remainingMs: number): string {
  const totalMin = Math.max(0, Math.floor(remainingMs / 60000));
  const d = Math.floor(totalMin / 1440);
  const h = Math.floor((totalMin % 1440) / 60);
  const m = totalMin % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m`;
}

export function expiryStatus(expiresAt: Date | string, now: Date | string): {
  urgency: ExpiryUrgency;
  label: string;
  tone: StatusTone;
  shape: StatusShape;
} {
  const ms = new Date(expiresAt).getTime() - new Date(now).getTime();
  const urgency = expiryUrgency(ms);
  if (urgency === "expired") return { urgency, label: "Expired", tone: "neutral", shape: "ring" };
  return {
    urgency,
    label: `Expires in ${formatRemaining(ms)}`,
    tone: urgency,
    shape: urgency === "warn" ? "half" : "full",
  };
}
