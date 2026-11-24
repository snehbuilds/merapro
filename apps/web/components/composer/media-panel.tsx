"use client";

import {
  estimateGeneration,
  generateMedia,
  getMediaJob,
  listPlatforms,
  mediaFromContentItem,
  suggestMediaAltText,
  uploadMedia,
  type EstimateOut,
  type JobOut,
  type MediaAspectOut,
  type PlatformOut,
} from "@mera/sdk";
import { useEffect, useId, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/field";
import { useSecondFactor } from "@/components/mera/second-factor";
import { Notice } from "@/components/ui/notice";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { MEDIA_ERROR_TEXT, mediaContentUrl, readMedia, type AttachedMedia, type MediaChoice } from "@/lib/media";
import { cn } from "@/lib/utils";

/*
 * composer.html Media for one account: the images this account's post carries,
 * in carousel order, each as the platform's own crop (the rendition is what an
 * approver sees and what goes out). Upload or take the article's preview image;
 * pick a shape the platform shows well; write alt text. "Save images" makes a
 * new version with the same text, because media is inside the content hash an
 * approval binds to.
 *
 * Generate (P2-M5): the cost is shown before anything is spent. Leave the prompt
 * blank and the Media agent plans the image(s) from this account's post, with alt
 * text; above the workspace's per-request ceiling a second factor is asked for, and
 * over the monthly budget the request is refused. Results join the list like an
 * upload: pick, reorder, then "Save images" makes the new version.
 */

const GEN_ASPECT: Record<string, "square" | "landscape" | "portrait"> = {
  instagram: "portrait",
  x: "landscape",
  linkedin_member: "landscape",
  linkedin_organization: "landscape",
  facebook: "landscape",
};
const DONE = new Set(["succeeded", "failed", "cancelled", "timed_out"]);

export function usd(v: string | number): string {
  const n = Number(v);
  return `$${n.toFixed(n > 0 && n < 0.1 ? 3 : 2)}`;
}

export interface MediaPanelProps {
  workspaceId: string;
  platform: string;
  media: readonly Record<string, unknown>[];
  /** The post set's article, when it has one: offers its preview image. */
  contentItemId: string | null;
  /** The account's post: lets the Media agent plan from it when no prompt is given. */
  variantId?: string;
  editable: boolean;
  onSave: (choices: MediaChoice[]) => Promise<{ ok: true } | { ok: false; message: string; code: string }>;
}

let platformsCache: Promise<PlatformOut[]> | null = null;

function loadPlatforms(): Promise<PlatformOut[]> {
  platformsCache ??= settle(listPlatforms({ client: browserApi })).then((r) => (r.ok ? r.data.data : []));
  return platformsCache;
}

export function MediaPanel({ workspaceId, platform, media, contentItemId, variantId, editable, onSave }: MediaPanelProps) {
  const id = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const [spec, setSpec] = useState<PlatformOut | null>(null);
  const [items, setItems] = useState<AttachedMedia[]>(() => readMedia(media));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { withSecondFactor } = useSecondFactor();
  const [genOpen, setGenOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(1);
  const [quality, setQuality] = useState<"low" | "medium" | "high">("medium");
  const [estimate, setEstimate] = useState<EstimateOut | null>(null);
  const [jobs, setJobs] = useState<JobOut[]>([]);
  const aspect = GEN_ASPECT[platform] ?? "square";

  useEffect(() => {
    if (!genOpen) return;
    let live = true;
    void settle(
      estimateGeneration({ client: browserApi, path: { workspace_id: workspaceId }, body: { count, quality, aspect } }),
    ).then((r) => {
      if (!live) return;
      if (r.ok) setEstimate(r.data);
      else setError(displayMessage(r.error));
    });
    return () => {
      live = false;
    };
  }, [genOpen, count, quality, aspect, workspaceId]);

  async function generate() {
    setBusy("generate");
    setError(null);
    const r = await withSecondFactor((headers) =>
      settle(
        generateMedia({
          client: browserApi,
          headers: { ...headers, ...idempotencyHeaders() },
          path: { workspace_id: workspaceId },
          body: {
            prompt: prompt.trim() || null,
            count,
            quality,
            aspect,
            post_variant_id: prompt.trim() ? null : (variantId ?? null),
            attach: false,
          },
        }),
      ),
    );
    if (!r.ok) {
      setBusy(null);
      setError(MEDIA_ERROR_TEXT[r.error.code] ?? displayMessage(r.error));
      return;
    }
    let current = r.data.data;
    setJobs(current);
    // Images take seconds; give up waiting after two minutes (the job keeps going).
    for (let tries = 0; tries < 80 && current.some((j) => !DONE.has(j.state)); tries++) {
      await new Promise((ok) => setTimeout(ok, 1500));
      current = await Promise.all(
        current.map(async (j) => {
          if (DONE.has(j.state)) return j;
          const g = await settle(getMediaJob({ client: browserApi, path: { workspace_id: workspaceId, job_id: j.id } }));
          return g.ok ? g.data : j;
        }),
      );
      setJobs(current);
    }
    setBusy(null);
    for (const j of current) {
      if (j.state === "succeeded" && j.result_asset_id) add({ id: j.result_asset_id, default_alt_text: j.alt_text ?? null });
    }
    const failed = current.find((j) => j.state !== "succeeded");
    if (failed) setError(failed.state === "running" || failed.state === "queued" ? "Still generating; it will appear in the library." : `Generation ${failed.state.replace("_", " ")}: ${failed.error ?? "no reason given"}.`);
  }

  // P2-M1.8: a vision model suggests; the person edits, then saves with the images.
  async function suggestAlt(i: number, masterId: string) {
    setBusy(`alt:${i}`);
    setError(null);
    const r = await settle(
      suggestMediaAltText({ client: browserApi, path: { workspace_id: workspaceId, asset_id: masterId }, body: { platform } }),
    );
    setBusy(null);
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    if (r.data.alt_text) update(i, { altText: r.data.alt_text });
  }

  useEffect(() => {
    let live = true;
    void loadPlatforms().then((all) => {
      if (live) setSpec(all.find((p) => p.platform === platform) ?? null);
    });
    return () => {
      live = false;
    };
  }, [platform]);

  const aspects: MediaAspectOut[] = spec?.media_aspects ?? [];
  const max = spec?.max_media ?? 4;
  const saved = readMedia(media);
  const dirty =
    items.length !== saved.length ||
    items.some((m, i) => m.masterId !== saved[i]?.masterId || m.aspect !== saved[i]?.aspect || (m.altText ?? "") !== (saved[i]?.altText ?? ""));
  const full = items.length >= max;

  function update(i: number, patch: Partial<AttachedMedia>) {
    setItems((list) => list.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  }

  function move(i: number, by: -1 | 1) {
    setItems((list) => {
      const next = [...list];
      const [m] = next.splice(i, 1);
      if (m) next.splice(i + by, 0, m);
      return next;
    });
  }

  function add(asset: { id: string; default_alt_text: string | null }) {
    setItems((list) =>
      list.some((m) => m.masterId === asset.id)
        ? list
        : [
            ...list,
            {
              masterId: asset.id,
              assetId: null,
              aspect: aspects[0]?.key ?? null,
              altText: asset.default_alt_text,
              width: null,
              height: null,
              mediaType: "image",
              contentType: null,
            },
          ],
    );
  }

  async function upload(file: File) {
    setBusy("upload");
    setError(null);
    const r = await settle(
      uploadMedia({ client: browserApi, headers: idempotencyHeaders(), path: { workspace_id: workspaceId }, body: { file } }),
    );
    setBusy(null);
    if (!r.ok) {
      setError(MEDIA_ERROR_TEXT[r.error.code] ?? displayMessage(r.error));
      return;
    }
    add(r.data);
  }

  async function fromArticle() {
    if (!contentItemId) return;
    setBusy("article");
    setError(null);
    const r = await settle(
      mediaFromContentItem({
        client: browserApi,
        headers: idempotencyHeaders(),
        path: { workspace_id: workspaceId },
        body: { content_item_id: contentItemId },
      }),
    );
    setBusy(null);
    if (!r.ok) {
      setError(MEDIA_ERROR_TEXT[r.error.code] ?? displayMessage(r.error));
      return;
    }
    add(r.data);
  }

  async function save() {
    setBusy("save");
    setError(null);
    const r = await onSave(items.map((m) => ({ asset_id: m.masterId, aspect: m.aspect, alt_text: m.altText ?? null })));
    setBusy(null);
    if (!r.ok) setError(MEDIA_ERROR_TEXT[r.code] ?? r.message);
  }

  return (
    <section aria-labelledby={`${id}-head`}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 id={`${id}-head`} className="text-sm font-semibold">
          Media
          <span className="ml-2 text-caption font-normal text-fg-subtle">
            {items.length} / {max}
            {spec?.requires_media ? " · at least one required" : ""}
          </span>
        </h3>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void upload(file);
            }}
          />
          <Button size="sm" disabled={!editable || full || busy !== null} onClick={() => fileRef.current?.click()}>
            <Icon name="upload" size="sm" />
            {busy === "upload" ? "Uploading…" : "Upload"}
          </Button>
          {contentItemId ? (
            <Button size="sm" disabled={!editable || full || busy !== null} onClick={() => void fromArticle()}>
              <Icon name="image" size="sm" />
              {busy === "article" ? "Fetching…" : "Article image"}
            </Button>
          ) : null}
          <Button size="sm" aria-expanded={genOpen} disabled={!editable || full} onClick={() => setGenOpen((o) => !o)}>
            <Icon name="bolt" size="sm" />
            Generate
          </Button>
        </div>
      </div>

      {genOpen ? (
        <div className="mb-2.5 grid gap-2 rounded-md border border-border p-2.5" role="group" aria-label="Generate images">
          <Label htmlFor={`${id}-prompt`} className="text-caption">
            Describe the image{variantId ? " (or leave blank: the Media agent plans from this post)" : ""}
          </Label>
          <Textarea
            id={`${id}-prompt`}
            rows={2}
            maxLength={1500}
            value={prompt}
            disabled={busy !== null}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <div className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1">
              <Label htmlFor={`${id}-count`} className="text-caption">
                Images
              </Label>
              <Select
                id={`${id}-count`}
                value={String(count)}
                disabled={busy !== null}
                onChange={(e) => setCount(Number(e.target.value))}
              >
                {Array.from({ length: Math.max(1, Math.min(10, max - items.length)) }, (_, i) => i + 1).map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </Select>
            </div>
            <div className="grid gap-1">
              <Label htmlFor={`${id}-quality`} className="text-caption">
                Quality
              </Label>
              <Select
                id={`${id}-quality`}
                value={quality}
                disabled={busy !== null}
                onChange={(e) => setQuality(e.target.value as "low" | "medium" | "high")}
              >
                <option value="low">Draft</option>
                <option value="medium">Standard</option>
                <option value="high">High</option>
              </Select>
            </div>
            <span className="flex-1" />
            <Button
              size="sm"
              variant="primary"
              disabled={busy !== null || !estimate || estimate.over_budget || (!prompt.trim() && !variantId)}
              onClick={() => void generate()}
            >
              {busy === "generate" ? "Generating…" : estimate ? `Generate · ${usd(estimate.cost_usd)}` : "Generate"}
            </Button>
          </div>
          {estimate ? (
            <p className="text-caption text-fg-subtle" aria-live="polite">
              About {usd(estimate.cost_usd)} for {estimate.count} image{estimate.count === 1 ? "" : "s"} · {usd(estimate.month_spent_usd)} of{" "}
              {usd(estimate.monthly_budget_usd)} used this month
              {estimate.over_budget
                ? ". This would go over the month's budget; an Admin can raise it in Settings."
                : estimate.needs_second_factor
                  ? `. Above the ${usd(estimate.request_ceiling_usd)} per-request ceiling: you'll confirm with a second factor.`
                  : "."}
            </p>
          ) : null}
          {jobs.length > 0 && busy === "generate" ? (
            <p className="text-caption text-fg-muted">
              {jobs.filter((j) => j.state === "succeeded").length} of {jobs.length} ready…
            </p>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <Notice tone="danger" className="mb-2">
          {error}
        </Notice>
      ) : null}

      {items.length === 0 ? (
        <p className="text-caption text-fg-subtle">
          {spec?.requires_media
            ? "This platform cannot post without an image. Upload one or use the article's image."
            : "No images. JPEG, PNG or WebP up to 20 MB; Mera crops each to this platform's shape and removes location data."}
        </p>
      ) : (
        <ol className="grid gap-2.5">
          {items.map((m, i) => (
            <li key={m.masterId} className="flex gap-3 rounded-md border border-border p-2.5">
              <div
                className={cn(
                  "grid w-20 shrink-0 place-items-center overflow-hidden rounded-sm bg-surface-sunken text-fg-subtle",
                  m.assetId ? "" : "border border-dashed border-border-strong",
                )}
                style={{ aspectRatio: aspectRatio(aspects, m.aspect) }}
              >
                {m.assetId ? (
                  // eslint-disable-next-line @next/next/no-img-element -- authenticated, same-origin bytes
                  <img src={mediaContentUrl(workspaceId, m.assetId)} alt={m.altText ?? ""} className="size-full object-cover" />
                ) : (
                  <span className="px-1 text-center text-[11px] leading-tight">Cropped on save</span>
                )}
              </div>
              <div className="grid min-w-0 flex-1 gap-1.5">
                <div className="flex flex-wrap items-end gap-2">
                  <div className="grid gap-1">
                    <Label htmlFor={`${id}-aspect-${i}`} className="text-caption">
                      Shape
                    </Label>
                    <Select
                      id={`${id}-aspect-${i}`}
                      value={m.aspect ?? ""}
                      disabled={!editable || busy !== null}
                      onChange={(e) => update(i, { aspect: e.target.value || null, assetId: e.target.value === m.aspect ? m.assetId : null })}
                    >
                      {aspects.map((a) => (
                        <option key={a.key} value={a.key}>
                          {a.key.replace("x", ":")} · {a.width}×{a.height}
                        </option>
                      ))}
                    </Select>
                  </div>
                  <span className="flex-1" />
                  <Button size="sm" variant="ghost" aria-label={`Move image ${i + 1} earlier`} disabled={!editable || i === 0} onClick={() => move(i, -1)}>
                    <Icon name="arrow" size="sm" className="rotate-180" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Move image ${i + 1} later`}
                    disabled={!editable || i === items.length - 1}
                    onClick={() => move(i, 1)}
                  >
                    <Icon name="arrow" size="sm" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove image ${i + 1}`}
                    disabled={!editable}
                    onClick={() => setItems((list) => list.filter((_, j) => j !== i))}
                  >
                    <Icon name="trash" size="sm" />
                  </Button>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor={`${id}-alt-${i}`} className="text-caption">
                    Alt text
                  </Label>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!editable || busy !== null}
                    onClick={() => void suggestAlt(i, m.masterId)}
                    aria-label={`Suggest alt text for image ${i + 1}`}
                  >
                    {busy === `alt:${i}` ? "Suggesting…" : "Suggest"}
                  </Button>
                </div>
                <Input
                  id={`${id}-alt-${i}`}
                  value={m.altText ?? ""}
                  maxLength={1000}
                  placeholder="Describe the image for people who can't see it"
                  disabled={!editable}
                  onChange={(e) => update(i, { altText: e.target.value })}
                />
              </div>
            </li>
          ))}
        </ol>
      )}

      {dirty ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Button size="sm" variant="primary" disabled={!editable || busy !== null} onClick={() => void save()}>
            {busy === "save" ? "Saving…" : "Save images"}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => setItems(saved)}>
            Discard
          </Button>
          <span className="text-caption text-fg-subtle">Saving makes a new version; an approval would need to be given again.</span>
        </div>
      ) : null}
    </section>
  );
}

function aspectRatio(aspects: readonly MediaAspectOut[], key: string | null): string {
  const a = aspects.find((x) => x.key === key) ?? aspects[0];
  return a ? `${a.width} / ${a.height}` : "1 / 1";
}
