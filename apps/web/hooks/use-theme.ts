"use client";

import { useSyncExternalStore } from "react";
import {
  getServerThemeSnapshot,
  getThemeSnapshot,
  setThemePreference,
  subscribeTheme,
} from "@/lib/theme";

export function useTheme() {
  const { preference, resolved } = useSyncExternalStore(
    subscribeTheme,
    getThemeSnapshot,
    getServerThemeSnapshot,
  );
  return { preference, resolved, setPreference: setThemePreference };
}
