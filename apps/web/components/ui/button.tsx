import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/* mera.css .btn, .btn--primary, .btn--ghost, .btn--danger, .btn--sm, .btn--icon */
const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border font-medium",
    "transition-[background-color,border-color,color] duration-[90ms] ease-out",
    "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-55",
    "aria-disabled:pointer-events-none aria-disabled:cursor-not-allowed aria-disabled:opacity-55",
  ],
  {
    variants: {
      variant: {
        default: [
          "border-border-strong bg-surface-raised text-fg",
          "shadow-[0_1px_0_color-mix(in_srgb,var(--border-strong)_45%,transparent)]",
          "hover:border-fg-subtle hover:bg-surface-hover active:bg-surface-active active:shadow-none",
        ],
        primary: [
          "border-accent bg-accent font-semibold text-accent-fg",
          "hover:border-accent-hover hover:bg-accent-hover hover:text-accent-fg",
        ],
        ghost: "border-transparent bg-transparent hover:border-transparent hover:bg-surface-active",
        danger: [
          "border-border-strong bg-surface-raised text-danger",
          "shadow-[0_1px_0_color-mix(in_srgb,var(--border-strong)_45%,transparent)]",
          "hover:border-danger hover:bg-danger-bg active:shadow-none",
        ],
      },
      size: {
        default: "h-8 px-3 text-sm",
        sm: "h-7 px-2.5 text-caption",
        icon: "size-8 p-0 text-sm",
        "icon-sm": "size-7 p-0 text-caption",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps extends ComponentProps<"button">, VariantProps<typeof buttonVariants> {
  /** Render the child element (e.g. a Next <Link>) with button styling. */
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, type, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      {...(asChild ? {} : { type: type ?? "button" })}
      {...props}
    />
  );
}

export { buttonVariants };
