import type { ComponentProps, ReactNode } from "react";
import {
  describeStatus,
  type StatusKind,
  type StatusModifiers,
  type StatusShape,
  type StatusStates,
  type StatusTone,
} from "@/lib/status";
import { cn } from "@/lib/utils";

/*
 * mera.css .status: shape + label, never colour alone.
 * Shapes: full (filled circle), ring (outline), half (half-filled), diamond.
 * Danger with the default "full" shape becomes a diamond, as in mera.css,
 * so danger always carries a second cue beyond hue.
 *
 * Two ways to use it:
 *   <StatusPill kind="post_variant" state="needs_approval" />   backend enum, looked up in lib/status.ts
 *   <StatusPill tone="warn" shape="half" label="Expires in 11h 42m" />   ad-hoc (timers, checks)
 * Prefer `kind` + `state` for anything that is a backend state.
 */
export type { StatusShape, StatusTone };

const TONE: Record<StatusTone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
  info: "text-info",
  neutral: "text-fg-subtle",
};

const SHAPE: Record<StatusShape, string> = {
  full: "rounded-full bg-current",
  ring: "rounded-full border-[1.5px] border-current bg-transparent",
  half: "rounded-full border-[1.5px] border-current bg-[linear-gradient(to_right,currentColor_50%,transparent_50%)]",
  diamond: "rounded-[1px] bg-current rotate-45 scale-90",
};

type BaseProps = Omit<ComponentProps<"span">, "children">;

export interface StatusPillManualProps extends BaseProps {
  tone: StatusTone;
  shape?: StatusShape;
  /** Required: the label is the primary carrier of meaning. */
  label: ReactNode;
  kind?: never;
  state?: never;
  modifiers?: never;
}

export type StatusPillEnumProps = BaseProps & {
  /** Overrides the derived label (rare: e.g. a timer next to the state). */
  label?: ReactNode;
  tone?: never;
  shape?: never;
  modifiers?: StatusModifiers;
} & { [K in StatusKind]: { kind: K; state: StatusStates[K] } }[StatusKind];

export type StatusPillProps = StatusPillManualProps | StatusPillEnumProps;

export function StatusPill(props: StatusPillProps) {
  let tone: StatusTone;
  let shape: StatusShape;
  let label: ReactNode;
  let rest: BaseProps;
  let dataState: string | undefined;

  if (props.kind !== undefined) {
    const { kind, state, modifiers, label: override, tone: _t, shape: _s, ...others } = props;
    void _t;
    void _s;
    const spec = describeStatus(kind, state as never, modifiers);
    tone = spec.tone;
    shape = spec.shape;
    label = override ?? spec.label;
    rest = others;
    dataState = `${kind}:${state}`;
  } else {
    const { tone: t, shape: sh = "full", label: l, kind: _k, state: _st, modifiers: _m, ...others } = props;
    void _k;
    void _st;
    void _m;
    tone = t;
    shape = sh;
    label = l;
    rest = others;
  }

  const resolved: StatusShape = tone === "danger" && shape === "full" ? "diamond" : shape;
  const { className, ...spanProps } = rest;
  return (
    <span
      data-slot="status-pill"
      data-tone={tone}
      data-shape={resolved}
      data-state={dataState}
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap text-caption font-medium",
        TONE[tone],
        className,
      )}
      {...spanProps}
    >
      <span aria-hidden="true" className={cn("size-2 shrink-0", SHAPE[resolved])} />
      {label}
    </span>
  );
}
