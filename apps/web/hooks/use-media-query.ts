"use client";

import { useCallback, useSyncExternalStore } from "react";

/** Subscribes to a media query. `serverValue` is used during SSR and hydration. */
export function useMediaQuery(query: string, serverValue = false): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const mq = window.matchMedia(query);
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => serverValue,
  );
}

/** Mirrors the mera.css ≤900px breakpoint where the rail goes off-canvas. */
export const MOBILE_RAIL_QUERY = "(max-width: 900px)";
