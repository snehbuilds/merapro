import type { ComponentProps } from "react";
import { Tag } from "@/components/ui/tag";
import { cn } from "@/lib/utils";

/*
 * accounts.html "What this account can do": read from the adapter's
 * capability descriptor, never hardcoded per platform (ADR-0008).
 *   supported     ok tag    "Publish"
 *   unsupported   neutral   "No threads"   (state the limitation, don't hide it)
 *   partial       warn      "Counts only, no impressions"
 * The tag text always says which, so tone is never the only cue.
 */
export interface PlatformCapabilities {
  supports_publish?: boolean;
  supports_images?: boolean;
  supports_video?: boolean;
  supports_alt_text?: boolean;
  supports_delete?: boolean;
  supports_threads?: boolean;
  supports_edit?: boolean;
  supports_scheduling?: boolean;
  /** "full" analytics, "counts" only (no impressions), or "none". */
  analytics?: "full" | "counts" | "none";
}

export type CapabilitySupport = "yes" | "no" | "partial";

export interface CapabilityTag {
  key: string;
  label: string;
  support: CapabilitySupport;
}

const FLAGS: { key: keyof PlatformCapabilities; yes: string; no: string }[] = [
  { key: "supports_publish", yes: "Publish", no: "No publishing" },
  { key: "supports_images", yes: "Images", no: "No images" },
  { key: "supports_video", yes: "Video", no: "No video" },
  { key: "supports_alt_text", yes: "Alt text", no: "No alt text" },
  { key: "supports_delete", yes: "Delete", no: "No deleting" },
  { key: "supports_threads", yes: "Threads", no: "No threads" },
  { key: "supports_edit", yes: "Editing", no: "No editing" },
  { key: "supports_scheduling", yes: "Scheduling", no: "No scheduling" },
];

const ORDER: Record<CapabilitySupport, number> = { yes: 0, no: 1, partial: 2 };

/** Descriptor → tags, supported first, then limitations, then partial support. */
export function capabilityTags(caps: PlatformCapabilities): CapabilityTag[] {
  const tags: CapabilityTag[] = [];
  for (const f of FLAGS) {
    const v = caps[f.key];
    if (v === undefined) continue;
    tags.push({ key: f.key, label: v ? f.yes : f.no, support: v ? "yes" : "no" });
  }
  if (caps.analytics === "full") tags.push({ key: "analytics", label: "Analytics", support: "yes" });
  if (caps.analytics === "counts")
    tags.push({ key: "analytics", label: "Counts only, no impressions", support: "partial" });
  if (caps.analytics === "none") tags.push({ key: "analytics", label: "No analytics", support: "no" });
  return tags.sort((a, b) => ORDER[a.support] - ORDER[b.support]);
}

const TONE = { yes: "ok", no: "neutral", partial: "warn" } as const;

export type CapabilityTagsProps = Omit<ComponentProps<"ul">, "children"> &
  (
    | { capabilities: PlatformCapabilities; tags?: never }
    /** Tags already derived from a descriptor (e.g. lib/accounts accountCapabilityTags). */
    | { tags: readonly CapabilityTag[]; capabilities?: never }
  );

export function CapabilityTags({ capabilities, tags, className, ...props }: CapabilityTagsProps) {
  const list = tags ?? capabilityTags(capabilities!);
  return (
    <ul
      data-slot="capability-tags"
      aria-label="What this account can do"
      className={cn("flex flex-wrap gap-2", className)}
      {...props}
    >
      {list.map((t) => (
        <li key={t.key}>
          <Tag tone={TONE[t.support]} data-support={t.support}>
            {t.label}
          </Tag>
        </li>
      ))}
    </ul>
  );
}
