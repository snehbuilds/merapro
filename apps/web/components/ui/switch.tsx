"use client";

import { useState, type ComponentProps } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .switch: a square-cornered track, role="switch" + aria-checked.
 * Controlled (checked + onCheckedChange) or uncontrolled (defaultChecked).
 * Name it with aria-label, or aria-labelledby / a <label htmlFor>.
 */
export interface SwitchProps
  extends Omit<ComponentProps<"button">, "role" | "aria-checked" | "onChange" | "value" | "defaultValue"> {
  checked?: boolean;
  defaultChecked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}

export function Switch({
  checked,
  defaultChecked = false,
  onCheckedChange,
  onClick,
  className,
  type,
  ...props
}: SwitchProps) {
  const [internal, setInternal] = useState(defaultChecked);
  const on = checked ?? internal;
  return (
    <button
      type={type ?? "button"}
      role="switch"
      aria-checked={on}
      data-slot="switch"
      data-state={on ? "on" : "off"}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        if (checked === undefined) setInternal(!on);
        onCheckedChange?.(!on);
      }}
      className={cn(
        "group/switch relative h-[18px] w-8 shrink-0 rounded-[3px] bg-border-strong transition-colors duration-[120ms] ease-out",
        "aria-checked:bg-accent disabled:cursor-not-allowed disabled:opacity-55",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className={cn(
          "absolute top-0.5 left-0.5 size-3.5 rounded-[2px] bg-surface-raised",
          "transition-transform duration-[140ms] ease-[cubic-bezier(.2,.8,.3,1)]",
          "group-aria-checked/switch:translate-x-3.5 group-aria-checked/switch:bg-accent-fg",
        )}
      />
    </button>
  );
}
