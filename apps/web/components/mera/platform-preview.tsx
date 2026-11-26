import type { ComponentProps, ReactNode } from "react";
import { Icon, PLATFORM_LABELS, type Platform } from "@/components/icons";
import { cn } from "@/lib/utils";
import { AccountMark } from "./account-chip";

/*
 * mera.css .preview: a generic post frame. Per-platform renderers (thread
 * parts, link cards, image grids) build on this frame later; for now every
 * platform shares it, with the platform named in the frame's label.
 *
 *   layout="post"     composer: author head, body with inline hashtags, media,
 *                     a foot note ("Renders as LinkedIn shows it · …").
 *   layout="compact"  approvals: no head, body, foot row with hashtags,
 *                     media summary and the counter.
 * threadParts renders a thread as numbered posts, hashtags on the last one, as
 * the platform posts it.
 * viewport="mobile" narrows the frame to a phone column (375px) and tightens
 * the body type, so line breaks fall where they will on a phone.
 */
export interface PreviewMedia {
  label: ReactNode;
  summary?: ReactNode;
  /** The image/video itself (same-origin); without it a placeholder shows the label. */
  src?: string;
  alt?: string;
  /** CSS aspect-ratio, e.g. "1080 / 1350". */
  ratio?: string;
  /** "image", "video", or "document". */
  mediaType?: string;
}

export interface PlatformPreviewProps extends Omit<ComponentProps<"figure">, "children"> {
  platform: Platform;
  author?: { name: ReactNode; subtitle?: ReactNode };
  body: string;
  /** A thread: each part is its own post. Replaces `body` in the frame. */
  threadParts?: readonly string[] | null;
  hashtags?: readonly string[];
  /** The first image (src) or a placeholder label like "1200 × 627". */
  media?: PreviewMedia | null;
  layout?: "post" | "compact";
  viewport?: "desktop" | "mobile";
  /** Compact: right side of the foot row (e.g. CharCounter). Post: the foot note. */
  foot?: ReactNode;
}

function Hashtags({ tags }: { tags: readonly string[] }) {
  return <span className="text-info">{tags.map((t) => (t.startsWith("#") ? t : `#${t}`)).join(" ")}</span>;
}

export function PlatformPreview({
  platform,
  author,
  body,
  threadParts,
  hashtags = [],
  media,
  layout = "post",
  viewport = "desktop",
  foot,
  className,
  ...props
}: PlatformPreviewProps) {
  const compact = layout === "compact";
  return (
    <figure
      data-slot="platform-preview"
      data-viewport={viewport}
      aria-label={`${PLATFORM_LABELS[platform]} preview${viewport === "mobile" ? ", mobile" : ""}`}
      className={cn(
        "overflow-hidden rounded-lg border border-border bg-surface-raised",
        viewport === "mobile" && "mx-auto w-full max-w-[375px]",
        className,
      )}
      {...props}
    >
      {!compact && author ? (
        <div className="flex items-center gap-2 border-b border-border px-3 py-[9px]">
          <AccountMark platform={platform} />
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold">{author.name}</span>
            {author.subtitle ? <span className="block truncate text-xs text-fg-subtle">{author.subtitle}</span> : null}
          </span>
        </div>
      ) : null}
      {threadParts && threadParts.length > 0 ? (
        <ol className="grid">
          {threadParts.map((part, i) => (
            <li
              key={i}
              className={cn(
                "border-b border-border p-3 leading-[1.6] break-words whitespace-pre-wrap last:border-b-0",
                viewport === "mobile" ? "text-sm" : "text-body",
              )}
            >
              <span className="mb-1 block font-mono text-2xs text-fg-subtle">
                {i + 1}/{threadParts.length}
              </span>
              {part}
              {!compact && i === threadParts.length - 1 && hashtags.length > 0 ? (
                <>
                  {" "}
                  <Hashtags tags={hashtags} />
                </>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <div
          className={cn(
            "p-3 leading-[1.6] break-words whitespace-pre-wrap",
            viewport === "mobile" ? "text-sm" : "text-body",
          )}
        >
          {body}
          {!compact && hashtags.length > 0 ? (
            <>
              {" "}
              <Hashtags tags={hashtags} />
            </>
          ) : null}
        </div>
      )}
      {media && !compact && media.src ? (
        <figure className="mx-3 mb-3 overflow-hidden rounded-md border border-border bg-surface-sunken">
          {media.mediaType === "video" ? (
            <video
              src={media.src}
              controls
              playsInline
              preload="metadata"
              aria-label={media.alt || "Video preview"}
              className="block max-h-[420px] w-full bg-black object-contain"
              style={media.ratio ? { aspectRatio: media.ratio } : undefined}
            />
          ) : media.mediaType === "document" ? (
            <div className="grid min-h-24 place-items-center gap-1.5 p-4 text-caption text-fg-muted">
              <Icon name="copy" size="lg" />
              <span>{media.alt || "PDF document"}</span>
            </div>
          ) : (
            /* eslint-disable-next-line @next/next/no-img-element -- authenticated, same-origin bytes */
            <img src={media.src} alt={media.alt ?? ""} className="block w-full object-cover" style={{ aspectRatio: media.ratio }} />
          )}
          <figcaption className="px-2.5 py-1.5 text-caption text-fg-subtle">{media.label}</figcaption>
        </figure>
      ) : media && !compact ? (
        <div className="mx-3 mb-3 grid min-h-28 place-items-center gap-1.5 rounded-md border border-dashed border-border-strong bg-surface-sunken p-4 text-caption text-fg-subtle">
          <Icon name="image" size="lg" />
          <span>{media.label}</span>
        </div>
      ) : null}
      {compact ? (
        <figcaption className="flex flex-wrap items-center gap-2.5 border-t border-border px-3 py-[9px] text-xs text-fg-subtle">
          {hashtags.length > 0 ? <Hashtags tags={hashtags} /> : null}
          <span className="flex-1" />
          {media ? (
            <span className="inline-flex items-center gap-[5px]">
              <Icon name="image" size="sm" />
              {media.summary ?? media.label}
            </span>
          ) : null}
          {foot}
        </figcaption>
      ) : foot ? (
        <figcaption className="flex items-center gap-2.5 border-t border-border px-3 py-[9px] text-xs text-fg-subtle">
          {foot}
        </figcaption>
      ) : null}
    </figure>
  );
}
