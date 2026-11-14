"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

/*
 * Live updates (M9.4, docs/04 §5): one EventSource per screen on the workspace's SSE
 * stream through the BFF. The browser reconnects on its own and resends Last-Event-ID,
 * so a laptop waking from sleep catches up instead of missing state. Matching events
 * trigger a debounced router.refresh(): the server components re-render with fresh data,
 * which is how a decision made in Telegram appears in the web queue within seconds.
 */
export function useWorkspaceEvents(workspaceId: string, types: string[], opts: { debounceMs?: number } = {}): void {
  const router = useRouter();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const key = types.join(",");
  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;
    const url = `/api/mera/workspaces/${workspaceId}/events?types=${encodeURIComponent(key)}`;
    const source = new EventSource(url);
    const onEvent = () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => router.refresh(), opts.debounceMs ?? 600);
    };
    const names = key.split(",").filter((t) => !t.includes("*"));
    source.onmessage = onEvent;
    for (const n of names) source.addEventListener(n, onEvent);
    // Glob subscriptions arrive as named events: listen to the common ones.
    for (const n of WILDCARD_EVENTS) if (key.split(",").some((g) => matches(g, n))) source.addEventListener(n, onEvent);
    return () => {
      source.close();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [workspaceId, key, router, opts.debounceMs]);
}

const WILDCARD_EVENTS = [
  "approval.requested",
  "approval.granted",
  "approval.rejected",
  "approval.vote",
  "approval.approved",
  "approval.partially_approved",
  "approval.rejected",
  "approval.expired",
  "approval.cancelled",
  "approval.superseded",
  "approval.reminded",
  "approval.escalated",
  "approval.edit_requested",
  "post.scheduled",
  "post.published",
  "post.failed",
  "post.dead_lettered",
  "post_set.published",
  "post_set.partially_published",
  "account.needs_reauth",
  "metrics.synced",
];

export function matches(glob: string, name: string): boolean {
  if (glob === "*") return true;
  if (glob.endsWith(".*")) return name.startsWith(glob.slice(0, -1));
  return glob === name;
}
