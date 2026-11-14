"use client";

import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/hooks/use-theme";

/*
 * mera.js [data-theme-toggle]: flips between light and dark, pinning an
 * explicit preference. "Follow system" is offered in the command palette
 * (and later on Settings), so the toggle stays a single, predictable button.
 */
export function ThemeToggle() {
  const { resolved, setPreference } = useTheme();
  const isDark = resolved === "dark";
  return (
    <Button
      variant="default"
      size="icon"
      aria-pressed={isDark}
      // A toggle keeps one name and reports state via aria-pressed; changing the
      // label as well (as mera.js does) makes screen readers announce it twice.
      aria-label="Dark theme"
      title={isDark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setPreference(isDark ? "light" : "dark")}
      className="shrink-0"
    >
      <Icon name={isDark ? "sun" : "moon"} />
    </Button>
  );
}
