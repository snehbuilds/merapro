"use client";

import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .seg. The mockup uses aria-pressed buttons; a one-of-N choice is a
 * radio group, so this follows the WAI-ARIA radio group pattern instead:
 * role="radiogroup" / role="radio" + aria-checked, one tab stop (roving
 * tabindex), arrow keys move and select, Home / End jump to the ends.
 */
export interface SegmentedOption<V extends string = string> {
  value: V;
  label: ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps<V extends string = string> {
  options: readonly SegmentedOption<V>[];
  value?: V;
  defaultValue?: V;
  onValueChange?: (value: V) => void;
  /** Required unless aria-labelledby is given: a radio group needs a name. */
  "aria-label"?: string;
  "aria-labelledby"?: string;
  className?: string;
}

export function SegmentedControl<V extends string = string>({
  options,
  value,
  defaultValue,
  onValueChange,
  className,
  ...aria
}: SegmentedControlProps<V>) {
  const [internal, setInternal] = useState<V | undefined>(defaultValue ?? options[0]?.value);
  const current = value ?? internal;
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const select = (next: V) => {
    if (value === undefined) setInternal(next);
    onValueChange?.(next);
  };

  const enabled = options.map((o, i) => ({ o, i })).filter(({ o }) => !o.disabled);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const pos = enabled.findIndex(({ i }) => i === index);
    let target: number | undefined;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        target = enabled[(pos + 1) % enabled.length]?.i;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        target = enabled[(pos - 1 + enabled.length) % enabled.length]?.i;
        break;
      case "Home":
        target = enabled[0]?.i;
        break;
      case "End":
        target = enabled[enabled.length - 1]?.i;
        break;
      default:
        return;
    }
    event.preventDefault();
    if (target === undefined) return;
    const option = options[target];
    if (!option) return;
    select(option.value);
    refs.current[target]?.focus();
  };

  const tabStop = options.some((o) => o.value === current && !o.disabled) ? current : enabled[0]?.o.value;

  return (
    <div
      role="radiogroup"
      data-slot="segmented-control"
      className={cn(
        "inline-flex rounded-md border border-border-strong bg-surface-sunken p-0.5",
        className,
      )}
      {...aria}
    >
      {options.map((option, index) => {
        const checked = option.value === current;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            disabled={option.disabled}
            tabIndex={option.value === tabStop ? 0 : -1}
            onClick={() => select(option.value)}
            onKeyDown={(e) => onKeyDown(e, index)}
            className={cn(
              "h-[26px] rounded-sm px-[11px] text-caption font-medium text-fg-muted",
              "hover:text-fg disabled:cursor-not-allowed disabled:opacity-55",
              "aria-checked:bg-surface-raised aria-checked:font-semibold aria-checked:text-fg",
              "aria-checked:shadow-[0_0_0_1px_var(--border-strong)]",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
