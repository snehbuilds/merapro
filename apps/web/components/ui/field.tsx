"use client";

import { createContext, useContext, useId, type ComponentProps, type ReactNode } from "react";
import { Icon } from "@/components/icons";
import { cn } from "@/lib/utils";

/*
 * mera.css .field / .input / .select / .textarea.
 *
 *   <Field label="Stagger" hint="Two X accounts in this group…">
 *     <Select>…</Select>
 *   </Field>
 *
 * Field owns the ids: the control gets id + aria-describedby (hint, then error)
 * + aria-invalid from context, so a label and hint are always wired. Label,
 * Hint, Input, Select and Textarea also work standalone.
 */

interface FieldContextValue {
  id: string;
  describedBy?: string;
  invalid: boolean;
}

const FieldContext = createContext<FieldContextValue | null>(null);

function useFieldControl(props: { id?: string; "aria-describedby"?: string; "aria-invalid"?: ComponentProps<"input">["aria-invalid"] }) {
  const ctx = useContext(FieldContext);
  const describedBy = [ctx?.describedBy, props["aria-describedby"]].filter(Boolean).join(" ") || undefined;
  return {
    id: props.id ?? ctx?.id,
    "aria-describedby": describedBy,
    "aria-invalid": props["aria-invalid"] ?? (ctx?.invalid ? true : undefined),
  };
}

export interface FieldProps extends Omit<ComponentProps<"div">, "children"> {
  label: ReactNode;
  hint?: ReactNode;
  /** Shown in danger ink below the control; also sets aria-invalid. */
  error?: ReactNode;
  /** Use a given id for the control instead of a generated one. */
  controlId?: string;
  children: ReactNode;
}

export function Field({ label, hint, error, controlId, className, children, ...props }: FieldProps) {
  const generated = useId();
  const id = controlId ?? `${generated}-control`;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <FieldContext.Provider value={{ id, describedBy, invalid: Boolean(error) }}>
      <div data-slot="field" className={cn("grid gap-1.5", className)} {...props}>
        <Label htmlFor={id}>{label}</Label>
        {children}
        {hint ? <Hint id={hintId}>{hint}</Hint> : null}
        {error ? (
          <p id={errorId} className="flex items-center gap-1.5 text-caption font-medium text-danger">
            <Icon name="alert" size="sm" />
            {error}
          </p>
        ) : null}
      </div>
    </FieldContext.Provider>
  );
}

export function Label({ className, ...props }: ComponentProps<"label">) {
  return <label data-slot="label" className={cn("text-sm font-semibold text-fg", className)} {...props} />;
}

export function Hint({ className, ...props }: ComponentProps<"span">) {
  return <span data-slot="hint" className={cn("text-caption text-fg-subtle", className)} {...props} />;
}

const controlBase = [
  "w-full min-h-8 rounded-md border border-border-strong bg-surface-raised px-2.5 py-[5px] text-body text-fg",
  "placeholder:text-fg-subtle",
  "focus-visible:outline-none focus:border-focus focus:shadow-[0_0_0_3px_var(--focus-ring)]",
  "aria-invalid:border-danger",
  "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:text-fg-subtle",
];

export function Input({ className, ...props }: ComponentProps<"input">) {
  const wired = useFieldControl(props);
  return <input data-slot="input" className={cn(controlBase, className)} {...props} {...wired} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  const wired = useFieldControl(props);
  return (
    <textarea
      data-slot="textarea"
      className={cn(controlBase, "min-h-24 resize-y leading-[1.6]", className)}
      {...props}
      {...wired}
    />
  );
}

/** Native <select> (keyboard, mobile pickers and autofill for free), styled. */
export function Select({ className, ...props }: ComponentProps<"select">) {
  const wired = useFieldControl(props);
  return (
    <span data-slot="select" className="relative block">
      <select className={cn(controlBase, "appearance-none pr-7", className)} {...props} {...wired} />
      <Icon
        name="chevdown"
        size="sm"
        className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-fg-muted"
      />
    </span>
  );
}
