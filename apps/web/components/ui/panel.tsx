import type { ComponentProps, ElementType, ReactNode } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .panel: a sheet laid on the paper.
 * tone="danger" is the settings/automation "Danger zone": a danger-tinted
 * border (34% mix, as in the mockups) and a danger-ink title. The fill stays
 * raised paper so the content inside still reads as ordinary controls.
 */
export interface PanelProps extends ComponentProps<"section"> {
  tone?: "default" | "danger";
}

export function Panel({ tone = "default", className, ...props }: PanelProps) {
  return (
    <section
      data-slot="panel"
      data-tone={tone}
      className={cn(
        "group/panel min-w-0 rounded-lg border border-border bg-surface-raised",
        tone === "danger" && "border-[color-mix(in_srgb,var(--danger)_34%,transparent)]",
        className,
      )}
      {...props}
    />
  );
}

export interface PanelHeadProps extends Omit<ComponentProps<"div">, "title"> {
  /** Rendered as a condensed, tracked column-head title. */
  title?: ReactNode;
  /** Heading level for the title. Defaults to h2; h4 for panels nested in a section. */
  as?: Extract<ElementType, "h2" | "h3" | "h4">;
  /** Right-aligned actions. */
  actions?: ReactNode;
}

export function PanelHead({ title, as: Heading = "h2", actions, className, children, ...props }: PanelHeadProps) {
  return (
    <div
      data-slot="panel-head"
      className={cn(
        "flex min-h-[42px] items-center gap-2.5 border-b border-border px-3.5 py-2 max-rail:flex-wrap max-rail:gap-y-2",
        className,
      )}
      {...props}
    >
      {title ? (
        <Heading className="font-label text-caption font-semibold uppercase tracking-[0.09em] group-data-[tone=danger]/panel:text-danger">
          {title}
        </Heading>
      ) : null}
      {children}
      {actions ? <div className="ml-auto flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function PanelBody({ flush = false, className, ...props }: ComponentProps<"div"> & { flush?: boolean }) {
  return <div data-slot="panel-body" className={cn(flush ? "p-0" : "p-3.5", className)} {...props} />;
}

export function PanelFoot({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="panel-foot"
      className={cn(
        "rounded-b-lg border-t border-dashed border-border-strong px-3.5 py-2.5 text-caption text-fg-muted",
        "[&_a]:font-medium [&_a]:text-accent",
        className,
      )}
      {...props}
    />
  );
}
