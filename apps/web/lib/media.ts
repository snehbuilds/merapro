/*
 * Media in the Composer (M5.14). A version's `media` is a list of plain objects
 * the API writes (asset_id = the platform rendition, master_id = the upload,
 * checksum, alt_text, aspect, width, height); read them defensively.
 */

export interface AttachedMedia {
  masterId: string;
  /** The rendition already attached; null until the next save crops it. */
  assetId: string | null;
  aspect: string | null;
  altText: string | null;
  width: number | null;
  height: number | null;
  mediaType: string;
  contentType: string | null;
}

/** One entry of PUT …/variants/{id}/media. */
export interface MediaChoice {
  asset_id: string;
  aspect: string | null;
  alt_text: string | null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function readMedia(media: readonly Record<string, unknown>[] | null | undefined): AttachedMedia[] {
  const out: AttachedMedia[] = [];
  for (const m of media ?? []) {
    const masterId = str(m.master_id);
    if (!masterId) continue;
    out.push({
      masterId,
      assetId: str(m.asset_id),
      aspect: str(m.aspect),
      altText: typeof m.alt_text === "string" ? m.alt_text : null,
      width: num(m.width),
      height: num(m.height),
      mediaType: str(m.media_type) ?? "image",
      contentType: str(m.content_type),
    });
  }
  return out;
}

/** Same-origin, through the session proxy: the API checks the workspace and role. */
export function mediaContentUrl(workspaceId: string, assetId: string): string {
  return `/api/mera/workspaces/${encodeURIComponent(workspaceId)}/media/${encodeURIComponent(assetId)}/content`;
}

/** Product copy for media error codes (the API's own message is the fallback). */
export const MEDIA_ERROR_TEXT: Record<string, string> = {
  unsupported_media_type: "Upload a JPEG, PNG or WebP image.",
  unreadable_image: "That file isn't a readable image.",
  animated_image: "Animated images aren't supported yet; upload a still.",
  image_too_small: "That image is too small; use one at least 200 pixels on each side.",
  image_too_large: "That image has too many pixels to process safely.",
  file_too_large: "Images can be at most 20 MB.",
  no_og_image: "This article has no preview image.",
  media_quota_exceeded: "This workspace's media storage is full; delete unused images first.",
  version_conflict: "This post changed since you opened it. Reload to see the latest, then try again.",
  unsupported_aspect: "This platform doesn't take that shape.",
  not_drafted: "Draft this account's text before adding images.",
  duplicate_media: "Each image can be attached once.",
  media_budget_spent: "This would go over the workspace's monthly media budget. An Admin can raise it in Settings.",
  unsafe_prompt: "Mera doesn't generate that kind of image. Describe something else.",
  video_disabled: "Video generation is switched off on this deployment.",
  prompt_required: "Describe the image you want.",
};
