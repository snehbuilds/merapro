"use client";

import { createWorkspace } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { formErrors } from "@/lib/api/errors";
import { screenHref } from "@/lib/nav";
import { SLUG_PATTERN, slugify } from "@/lib/workspace";

const FIELDS = ["name", "slug", "timezone"] as const;
type WorkspaceField = (typeof FIELDS)[number];

const noopSubscribe = () => () => {};

function browserTimeZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

let zoneCache: readonly string[] | null = null;
function zoneList(): readonly string[] {
  if (!zoneCache) {
    let zones: string[] = [];
    try {
      zones = Intl.supportedValuesOf("timeZone");
    } catch {
      // Older engines: the browser's own zone and UTC are still offered.
    }
    zoneCache = Array.from(new Set(["UTC", ...zones])).sort();
  }
  return zoneCache;
}
const SERVER_ZONES: readonly string[] = ["UTC"];

/*
 * POST /workspaces through the BFF. The slug follows the name until the user
 * edits it; the timezone defaults to the browser's IANA zone (read after
 * hydration, so server and client markup agree).
 */
export interface CreateWorkspaceFormProps {
  onSuccess?: (slug: string) => void;
  onCancel?: () => void;
}

export function CreateWorkspaceForm({ onSuccess, onCancel }: CreateWorkspaceFormProps = {}) {
  const router = useRouter();
  const detectedZone = useSyncExternalStore(noopSubscribe, browserTimeZone, () => null);
  const zones = useSyncExternalStore(noopSubscribe, zoneList, () => SERVER_ZONES);

  const [name, setName] = useState("");
  const [slugEdited, setSlugEdited] = useState<string | null>(null);
  const [zoneChosen, setZoneChosen] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<WorkspaceField, string>>>({});
  const [formError, setFormError] = useState<string[]>([]);

  const slug = slugEdited ?? slugify(name);
  const timezone = zoneChosen ?? detectedZone ?? "UTC";
  const zoneOptions = zones.includes(timezone) ? zones : [timezone, ...zones];

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFieldErrors({});
    setFormError([]);
    if (!SLUG_PATTERN.test(slug)) {
      setFieldErrors({ slug: "Use 1–48 lowercase letters, digits and hyphens, starting and ending with a letter or digit." });
      return;
    }
    setPending(true);
    const result = await settle(
      createWorkspace({ client: browserApi, headers: idempotencyHeaders(), body: { name: name.trim(), slug, timezone } }),
    );
    if (result.ok) {
      if (onSuccess) {
        onSuccess(result.data.slug);
      } else {
        router.push(screenHref(result.data.slug, "dashboard"));
        router.refresh();
      }
      return;
    }
    setPending(false);
    const split = formErrors(result.error, FIELDS);
    setFieldErrors(split.fields);
    setFormError(split.form);
  }

  return (
    <form className="grid gap-4" onSubmit={onSubmit} noValidate>
      {formError.length > 0 ? (
        <Notice tone="danger" role="alert">
          {formError.join(" ")}
        </Notice>
      ) : null}
      <Field label="Workspace name" error={fieldErrors.name}>
        <Input
          name="name"
          required
          maxLength={120}
          autoFocus
          autoComplete="organization"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field
        label="URL"
        hint={
          <>
            Your workspace lives at <span className="font-mono">/w/{slug || "…"}</span>
          </>
        }
        error={fieldErrors.slug}
      >
        <Input
          name="slug"
          required
          maxLength={48}
          spellCheck={false}
          autoCapitalize="none"
          className="font-mono"
          value={slug}
          onChange={(e) => setSlugEdited(e.target.value.toLowerCase())}
        />
      </Field>
      <Field label="Timezone" hint="Posting times and the calendar use this zone." error={fieldErrors.timezone}>
        <Select name="timezone" value={timezone} onChange={(e) => setZoneChosen(e.target.value)}>
          {zoneOptions.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </Select>
      </Field>
      <div className={onCancel ? "flex items-center justify-end gap-2 pt-1" : ""}>
        {onCancel ? (
          <Button type="button" variant="default" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          disabled={pending || !name.trim()}
          className={onCancel ? "" : "w-full"}
        >
          {pending ? "Creating workspace…" : "Create workspace"}
        </Button>
      </div>
    </form>
  );
}
