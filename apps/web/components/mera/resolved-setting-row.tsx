import type { ComponentProps, ReactNode } from "react";
import { Tag } from "@/components/ui/tag";
import { cn } from "@/lib/utils";

/*
 * groups.html "Resolved settings": each effective value and where it came
 * from. Two resolution rules (docs/03 settings resolution):
 *   most_specific   post > account > group > workspace > system default;
 *                   shown as quiet text.
 *   strictest_wins  approval policy and autopilot mode resolve to the
 *                   strictest applicable value; shown as a warn tag, because
 *                   it is the rule people do not expect.
 */
export type SettingLevel = "post" | "account" | "group" | "workspace" | "system";
export type ResolutionRule = "most_specific" | "strictest_wins";

export const SETTING_LEVEL_LABEL: Record<SettingLevel, string> = {
  post: "Post",
  account: "Account",
  group: "Group",
  workspace: "Workspace",
  system: "System default",
};

export const RESOLUTION_RULE_LABEL: Record<ResolutionRule, string> = {
  most_specific: "Most specific",
  strictest_wins: "Strictest wins",
};

export interface ResolvedSettingRowProps extends Omit<ComponentProps<"tr">, "children"> {
  setting: ReactNode;
  value: ReactNode;
  source: SettingLevel;
  rule: ResolutionRule;
}

const td = "border-b border-border px-3 py-2.5 align-middle first:pl-3.5 last:pr-3.5";

export function ResolvedSettingRow({ setting, value, source, rule, className, ...props }: ResolvedSettingRowProps) {
  return (
    <tr data-slot="resolved-setting-row" className={cn("hover:bg-surface-hover", className)} {...props}>
      <th scope="row" className={cn(td, "text-left font-normal")}>
        {setting}
      </th>
      <td className={td}>{value}</td>
      <td className={td}>
        <Tag tone="outline">{SETTING_LEVEL_LABEL[source]}</Tag>
      </td>
      <td className={td}>
        {rule === "strictest_wins" ? (
          <Tag tone="warn">{RESOLUTION_RULE_LABEL[rule]}</Tag>
        ) : (
          <span className="text-caption text-fg-subtle">{RESOLUTION_RULE_LABEL[rule]}</span>
        )}
      </td>
    </tr>
  );
}

const th =
  "border-b border-border-strong px-3 py-[9px] text-left font-label text-[11.5px] font-semibold tracking-[0.09em] whitespace-nowrap text-fg-subtle uppercase first:pl-3.5 last:pr-3.5";

/** The table the rows sit in, with the mockup's column heads. */
export function ResolvedSettingsTable({
  caption,
  children,
  className,
}: {
  caption?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className="relative overflow-x-auto">
      <table data-slot="resolved-settings" className={cn("w-full border-collapse text-sm [&_tbody_tr:last-child>*]:border-b-0", className)}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            <th scope="col" className={th}>
              Setting
            </th>
            <th scope="col" className={th}>
              Effective value
            </th>
            <th scope="col" className={th}>
              Inherited from
            </th>
            <th scope="col" className={th}>
              Rule
            </th>
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
