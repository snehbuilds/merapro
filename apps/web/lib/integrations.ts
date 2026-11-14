/* Digests, webhooks, exports and security alerts (P3-M4, P3-M7): copy and small rules. */

export const DIGEST_MODES = [
  { value: "immediate", label: "Each post as it arrives" },
  { value: "digest", label: "One daily digest" },
  { value: "both", label: "Both" },
] as const;

/** What the event outbox carries (WebhookService.EVENT_TYPES). */
export const WEBHOOK_EVENTS = [
  "post.published",
  "post.failed",
  "post.dead_lettered",
  "post.scheduled",
  "approval.requested",
  "approval.granted",
  "approval.edit_requested",
  "approval.escalated",
  "approval.expired",
  "approval.cancelled",
  "approval.reminded",
  "account.needs_reauth",
  "metrics.synced",
] as const;

export const ALERT_COPY: Record<string, string> = {
  "mfa.locked": "Two-step sign-in locked after wrong codes",
  "pin.locked": "Approval PIN locked after wrong tries",
  "refresh.reuse_detected": "A sign-in token was reused; that session was ended",
  "second_factor.burst": "Several failed confirmations in a few minutes",
  "autopilot.changed": "Autopilot mode changed",
};

export function isHHMM(v: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(v.trim());
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
