import type { EmojiPolicy } from "@mera/sdk";
import type { Voice, VoiceScopeType } from "@/lib/api/models";

/*
 * Brand Voice view logic (brand-voice.html): the three scopes, the editor
 * form's fields and the error copy. Resolution is account, then group, then
 * workspace (backend drafting.core.resolve_voice); "default" is per scope.
 */

export const SCOPE_ORDER: readonly VoiceScopeType[] = ["workspace", "account_group", "social_account"];

export const SCOPE_LABEL: Readonly<Record<VoiceScopeType, string>> = {
  workspace: "Workspace",
  account_group: "Group",
  social_account: "Account",
};

/** List section headings. */
export const SCOPE_SECTION: Readonly<Record<VoiceScopeType, string>> = {
  workspace: "Workspace",
  account_group: "Groups",
  social_account: "Accounts",
};

export function groupVoicesByScope<T extends Pick<Voice, "scope_type" | "scope_name" | "name" | "is_default">>(
  voices: readonly T[],
): { scope: VoiceScopeType; voices: T[] }[] {
  return SCOPE_ORDER.map((scope) => ({
    scope,
    voices: voices
      .filter((v) => v.scope_type === scope)
      .sort(
        (a, b) =>
          a.scope_name.localeCompare(b.scope_name) || Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name),
      ),
  })).filter((g) => g.voices.length > 0);
}

/** "Applies to Work LinkedIn", "Applies to the whole workspace". */
export function appliesTo(voice: Pick<Voice, "scope_type" | "scope_name">): string {
  return voice.scope_type === "workspace" ? "Applies to the whole workspace" : `Applies to ${voice.scope_name}`;
}

/** The list row's second line: "Work LinkedIn · direct, first person". */
export function voiceSummary(voice: Pick<Voice, "scope_type" | "scope_name" | "is_default" | "tone">): string {
  const where = voice.scope_type === "workspace" ? (voice.is_default ? "Default" : "Workspace") : voice.scope_name;
  const tone = voice.tone?.split(/[.\n]/)[0]?.trim();
  return tone ? `${where} · ${tone}` : where;
}

export const EMOJI_POLICIES: readonly { value: EmojiPolicy; label: string }[] = [
  { value: "none", label: "None" },
  { value: "sparing", label: "Sparing" },
  { value: "liberal", label: "Liberal" },
];

/** brand-voice.html CTA choices. cta_style is free text, so a stored value outside these is kept as is. */
export const CTA_STYLES: readonly { value: string; label: string }[] = [
  { value: "", label: "None — let the post stand" },
  { value: "Soft question", label: "Soft question" },
  { value: "Explicit link", label: "Explicit link" },
];

export const MAX_EXAMPLES = 10;
export const MAX_STORIES = 12;
export const MAX_BANNED_WORDS = 100;

/** Fields the voice form shows inline. */
export const VOICE_FORM_FIELDS = [
  "name",
  "tone",
  "target_audience",
  "emoji_policy",
  "cta_style",
  "banned_words",
  "example_posts",
  "about",
  "story_bank",
  "scope_type",
  "scope_id",
] as const;
export type VoiceFormField = (typeof VOICE_FORM_FIELDS)[number];

/** Copy for brand-voice API errors that carry no field. */
export const VOICE_ERROR_TEXT: Readonly<Record<string, string>> = {
  forbidden:
    "You can't change voices at that scope. Editors and up edit voices, and a scoped editor only for their own groups and accounts.",
  workspace_archived: "This workspace is archived, so its voices can't change.",
  not_found: "That voice no longer exists. Reload to see the current list.",
  voice_limit_reached: "This workspace has reached its voice limit. Delete one you no longer use first.",
  invalid_scope: "Pick a group or an account for this voice.",
  unknown_group: "That group no longer exists. Reload the page.",
  unknown_account: "That account no longer exists. Reload the page.",
  service_unavailable: "The Writer is unavailable right now, so a sample can't be drafted. Try again in a moment.",
};
