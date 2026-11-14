import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps, ReactNode } from "react";
import { Icon, type IconName } from "@/components/icons";
import { cn } from "@/lib/utils";

/*
 * mera.css .notice: an inline note on the page, tinted by tone, with an icon
 * in the tone's ink. Body text stays muted ink so tinted backgrounds keep AA.
 * `title` renders as the strong lead; `actions` is a row of small buttons
 * under the text.
 */
const noticeVariants = cva(
  "flex items-start gap-2.5 rounded-md border px-3.5 py-[11px] text-sm text-fg-muted [&_strong]:font-semibold [&_strong]:text-fg",
  {
    variants: {
      tone: {
        default: "border-border bg-surface-raised [&>[data-slot=icon]]:text-fg-subtle",
        warn: "border-[color-mix(in_srgb,var(--warn)_30%,transparent)] bg-warn-bg [&>[data-slot=icon]]:text-warn",
        danger: "border-[color-mix(in_srgb,var(--danger)_30%,transparent)] bg-danger-bg [&>[data-slot=icon]]:text-danger",
        info: "border-[color-mix(in_srgb,var(--info)_30%,transparent)] bg-info-bg [&>[data-slot=icon]]:text-info",
      },
    },
    defaultVariants: { tone: "default" },
  },
);

const DEFAULT_ICON: Record<NonNullable<NoticeProps["tone"]>, IconName> = {
  default: "info",
  warn: "alert",
  danger: "alert",
  info: "info",
};

export interface NoticeProps extends Omit<ComponentProps<"div">, "title">, VariantProps<typeof noticeVariants> {
  title?: ReactNode;
  /** Override the tone's icon, or pass null for none. */
  icon?: IconName | null;
  /** A row of actions under the text (small buttons, links). */
  actions?: ReactNode;
}

export function Notice({ tone, title, icon, actions, className, children, ...props }: NoticeProps) {
  const iconName = icon === undefined ? DEFAULT_ICON[tone ?? "default"] : icon;
  return (
    <div data-slot="notice" data-tone={tone ?? "default"} className={cn(noticeVariants({ tone }), className)} {...props}>
      {iconName ? <Icon name={iconName} size="sm" className="mt-0.5" /> : null}
      <div className="min-w-0 flex-1">
        {title ? <strong>{title} </strong> : null}
        {children}
        {actions ? <div className="mt-2.5 flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
