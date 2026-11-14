import type { AccountOut, GroupOut, Platform as ApiPlatform } from "@mera/sdk";
import type { HashtagSuggestion, PostSet, SkippedTarget, Variant, Version } from "@/lib/api/models";
import { platformLabel } from "@/lib/accounts";
import { countGraphemes } from "@/lib/graphemes";
import { POST_SET_STATES, POST_VARIANT_STATES, type PostSetState, type PostVariantState, type StatusTone } from "@/lib/status";

/*
 * Composer view logic (composer.html, docs/12 §4): platform rules for the
 * counter and preview, the agent's notes and the Editor's issues read out of
 * their JSON columns, guardrail verdicts in words, the target picker's
 * predictions and the error copy for post-set calls. Everything the API
 * decides (expansion, counts, guardrails) is shown as the API returned it;
 * the client only predicts where the user is still typing.
 */

/* ---- platform rules ---------------------------------------------------- */

export interface PlatformRule {
  /** PlatformCapabilities.max_chars. */
  limit: number;
  /** PlatformCapabilities.conventional_hashtags. */
  hashtags: readonly [number, number];
  /** Bluesky counts grapheme clusters; the others count code points. */
  countsGraphemes: boolean;
  /** X weighs every URL as 23 characters. */
  urlWeight?: number;
}

/**
 * Mirrors the adapter capability descriptors (backend
 * infrastructure/platforms/capabilities.py). The saved version's char_limit
 * is the authority; this table drives the live counter while typing and the
 * preview's convention line.
 */
export const PLATFORM_RULES: Readonly<Record<ApiPlatform, PlatformRule>> = {
  bluesky: { limit: 300, hashtags: [1, 2], countsGraphemes: true },
  mastodon: { limit: 500, hashtags: [2, 3], countsGraphemes: false },
  linkedin_member: { limit: 3000, hashtags: [0, 2], countsGraphemes: false },
  linkedin_organization: { limit: 3000, hashtags: [0, 2], countsGraphemes: false },
  x: { limit: 280, hashtags: [0, 1], countsGraphemes: false, urlWeight: 23 },
  threads: { limit: 500, hashtags: [0, 1], countsGraphemes: false },
  instagram: { limit: 2200, hashtags: [3, 5], countsGraphemes: false },
  facebook: { limit: 63206, hashtags: [0, 2], countsGraphemes: false },
};

export function platformRule(platform: ApiPlatform): PlatformRule {
  return PLATFORM_RULES[platform] ?? { limit: 3000, hashtags: [1, 3], countsGraphemes: false };
}

const URL_PATTERN = /https?:\/\/\S+/gi;

/** As the platform counts it (backend text_checks.weighted_length). */
export function weightedLength(text: string, platform: ApiPlatform): number {
  const rule = platformRule(platform);
  if (rule.urlWeight) return Array.from(text.replace(URL_PATTERN, "x".repeat(rule.urlWeight))).length;
  return rule.countsGraphemes ? countGraphemes(text) : Array.from(text).length;
}

/** The text as it will be posted: body, then the tags on their own line. */
export function composeText(body: string, hashtags: readonly string[]): string {
  const tags = hashtags.map((t) => `#${t}`).join(" ");
  return tags ? `${body.trimEnd()}\n\n${tags}` : body.trimEnd();
}

/** "3,000 character limit · 3–5 hashtags conventional". */
export function conventionLine(platform: ApiPlatform, limit = platformRule(platform).limit): string {
  const [lo, hi] = platformRule(platform).hashtags;
  const tags = lo === hi ? `${lo}` : `${lo}–${hi}`;
  return `${limit.toLocaleString("en-US")} character limit · ${tags} hashtag${hi === 1 ? "" : "s"} conventional`;
}

/** "#Postgres 18!" → "postgres18" (backend text_checks.normalize_hashtag). null when nothing usable remains. */
export function normalizeHashtag(tag: string): string | null {
  const t = tag.trim().replace(/^#+/, "").normalize("NFKC").replace(/[^\p{L}\p{N}_]/gu, "").toLowerCase();
  return t.slice(0, 60) || null;
}

/* ---- state ------------------------------------------------------------- */

export function asVariantState(state: string): PostVariantState {
  return (POST_VARIANT_STATES as readonly string[]).includes(state) ? (state as PostVariantState) : "drafting";
}

export function asPostSetState(state: string): PostSetState {
  return (POST_SET_STATES as readonly string[]).includes(state) ? (state as PostSetState) : "drafting";
}

/**
 * Variant states whose content can still change (the API answers variant_locked otherwise).
 * Editing an account in approval, approved or rejected makes a new version: the pending
 * approval is superseded, and an earlier approval does not cover the new text (ADR-0014).
 */
export const EDITABLE_VARIANT_STATES: readonly string[] = ["drafting", "blocked", "skipped", "needs_approval", "approved", "rejected"];

export function isVariantEditable(variant: Pick<Variant, "state">): boolean {
  return EDITABLE_VARIANT_STATES.includes(variant.state);
}

/** Post-set states that can still be archived (post_set_locked otherwise). */
export function isSetArchivable(set: Pick<PostSet, "state">): boolean {
  return set.state === "drafting" || set.state === "blocked";
}

/**
 * Voices a variant may use (voice_not_applicable otherwise): any workspace
 * voice, its routing group's, and its own account's.
 */
export function applicableVoices<T extends { scope_type: string; scope_id: string | null }>(
  voices: readonly T[],
  variant: Pick<Variant, "via_account_group_id" | "social_account_id">,
): T[] {
  return voices.filter(
    (v) =>
      v.scope_type === "workspace" ||
      (v.scope_type === "account_group" && v.scope_id !== null && v.scope_id === variant.via_account_group_id) ||
      (v.scope_type === "social_account" && v.scope_id === variant.social_account_id),
  );
}

/** API bounds on version content. */
export const MAX_THREAD_PART_CHARS = 2000;

/** Drafting is queued or running on a worker: poll until it settles. */
export function isGenerating(set: Pick<PostSet, "generation_status">): boolean {
  return set.generation_status === "queued" || set.generation_status === "running";
}

export const POLL_INTERVAL_MS = 2000;

/**
 * Status line while a job runs; null when idle. The API does not say which accounts a job
 * covers (a rewrite may touch one), so no count is claimed.
 */
export function generationText(set: Pick<PostSet, "generation_status">): string | null {
  if (set.generation_status === "queued") return "Queued — drafting starts in a moment…";
  if (set.generation_status === "running") return "Drafting…";
  return null;
}

/** Variants in publish order. */
export function orderedVariants<T extends Pick<Variant, "position">>(variants: readonly T[]): T[] {
  return [...variants].sort((a, b) => a.position - b.position);
}

/** Confidence tag tone: ok ≥ 0.8, warn ≥ 0.6, danger below. */
export function confidenceTone(score: number): "ok" | "warn" | "danger" {
  if (score >= 0.8) return "ok";
  if (score >= 0.6) return "warn";
  return "danger";
}

/** The first line or so of a version, for the account list. */
export function excerpt(version: Pick<Version, "body" | "thread_parts"> | null, max = 110): string {
  if (!version) return "";
  const text = (version.thread_parts?.[0] ?? version.body).replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

/* ---- agent notes and Editor issues -------------------------------------- */

export interface AgentNotes {
  angle?: string;
  hookType?: string;
  selfCritique?: string;
  voiceName?: string;
  voiceSource?: string;
  voiceMatch?: number;
  confidenceRationale?: string;
  differentiateAttempts?: number;
  closestSiblingSimilarity?: number;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}
function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** agent_notes is free-form JSON; read only what is there and well-typed. */
export function readNotes(version: Pick<Version, "agent_notes"> | null): AgentNotes {
  const n = (version?.agent_notes ?? {}) as Record<string, unknown>;
  return {
    angle: str(n.angle),
    hookType: str(n.hook_type),
    selfCritique: str(n.self_critique),
    voiceName: str(n.voice_name),
    voiceSource: str(n.voice_source),
    voiceMatch: num(n.voice_match),
    confidenceRationale: str(n.confidence_rationale),
    differentiateAttempts: num(n.differentiate_attempts),
    closestSiblingSimilarity: num(n.closest_sibling_similarity),
  };
}

/** Where the voice came from (backend drafting.core.resolve_voice). */
export const VOICE_SOURCE_LABEL: Readonly<Record<string, string>> = {
  variant: "chosen for this post",
  account: "from account",
  group: "from group",
  workspace: "workspace default",
  "built-in": "built-in default",
};

export function voiceSourceLabel(source: string | undefined): string | undefined {
  if (!source) return undefined;
  return VOICE_SOURCE_LABEL[source] ?? source.replace(/_/g, " ");
}

export const HOOK_TYPE_LABEL: Readonly<Record<string, string>> = {
  question: "Question",
  statistic: "Statistic",
  contrarian: "Contrarian",
  story: "Story",
  announcement: "Announcement",
};

export interface EditorIssue {
  category: string;
  severity: "info" | "warning" | "block";
  excerpt?: string;
  explanation?: string;
  suggestedFix?: string;
}

export function readIssues(version: Pick<Version, "compliance_flags"> | null): EditorIssue[] {
  return (version?.compliance_flags ?? []).flatMap((raw) => {
    const f = raw as Record<string, unknown>;
    const severity = f.severity === "block" || f.severity === "warning" ? f.severity : "info";
    const category = str(f.category);
    if (!category) return [];
    return [{ category, severity, excerpt: str(f.excerpt), explanation: str(f.explanation), suggestedFix: str(f.suggested_fix) }];
  });
}

export const ISSUE_CATEGORY_LABEL: Readonly<Record<string, string>> = {
  factual: "Factual",
  tone: "Tone",
  brand_safety: "Brand safety",
  pii: "Personal data",
  copyright: "Copyright",
  platform_policy: "Platform policy",
  banned_word: "Banned word",
  attribution: "Attribution",
  cross_account_duplicate: "Too close to another account",
  account_mismatch: "Wrong account",
};

export function issueCategoryLabel(category: string): string {
  return ISSUE_CATEGORY_LABEL[category] ?? category.charAt(0).toUpperCase() + category.slice(1).replace(/_/g, " ");
}

export interface ValidationIssue {
  code: string;
  severity: "warn" | "block";
  message: string;
}

export function readValidation(version: Pick<Version, "validation"> | null): ValidationIssue[] {
  return (version?.validation ?? []).flatMap((raw) => {
    const v = raw as Record<string, unknown>;
    const message = str(v.message);
    if (!message) return [];
    return [{ code: str(v.code) ?? "issue", severity: v.severity === "block" ? "block" : "warn", message }];
  });
}

/* ---- hashtags ---------------------------------------------------------- */

/** Stored suggestions (version.hashtag_suggestions) or a suggest response, validated. */
export function readSuggestions(raw: readonly unknown[]): HashtagSuggestion[] {
  return raw.flatMap((r) => {
    const s = r as Record<string, unknown>;
    const tag = str(s.tag);
    if (!tag) return [];
    return [
      {
        tag,
        rank: num(s.rank) ?? 0,
        basis: str(s.basis) ?? "content_only",
        sample_size: num(s.sample_size) ?? 0,
        lift_vs_account_median: num(s.lift_vs_account_median) ?? null,
        rationale: str(s.rationale) ?? "",
      },
    ];
  });
}

export const HASHTAG_BASIS_LABEL: Readonly<Record<string, string>> = {
  history: "Measured",
  platform_convention: "Platform convention",
  content_only: "Content only",
};

export function basisLabel(basis: string): string {
  return HASHTAG_BASIS_LABEL[basis] ?? basis.replace(/_/g, " ");
}

/** Fewer posts than this and the lift is noise; the API sends no lift below it (P3-M1.1). */
export const MIN_POSTS_FOR_LIFT = 10;

/**
 * "+34%" from a measured lift, which the API sends as a ratio to the account's
 * typical post (1.34). null (shown as "—") when the API has no measurement, or
 * the sample is too small to mean anything. Never estimated.
 */
export function formatLift(s: Pick<HashtagSuggestion, "lift_vs_account_median" | "sample_size">): string | null {
  const lift = s.lift_vs_account_median;
  if (lift === null || s.sample_size < MIN_POSTS_FOR_LIFT) return null;
  const pct = Math.round((lift - 1) * 100);
  return `${pct >= 0 ? "+" : "−"}${Math.abs(pct)}%`;
}

/* ---- guardrails -------------------------------------------------------- */

export const GUARDRAIL_LABEL: Readonly<Record<string, string>> = {
  banned_words: "Banned words",
  pii: "Personal data",
  platform_policy: "Platform rules",
  duplicate_post: "Repeats an earlier post",
  editor_review: "Editor review",
  cross_account_duplicate: "Too close to another account",
};

export function guardrailLabel(key: string): string {
  return GUARDRAIL_LABEL[key] ?? key.replace(/_/g, " ");
}

export interface GuardrailReason {
  key: string;
  outcome: "warn" | "block";
  text: string;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** A variant's warn / block verdicts, each with the reason in words. Blocks first. */
export function variantGuardrailReasons(variant: Pick<Variant, "guardrails">): GuardrailReason[] {
  const out: GuardrailReason[] = [];
  for (const g of variant.guardrails) {
    if (g.outcome !== "warn" && g.outcome !== "block") continue;
    const d = g.details as Record<string, unknown>;
    let text: string;
    switch (g.guardrail_key) {
      case "banned_words": {
        const found = list(d.found).filter((x): x is string => typeof x === "string");
        text = found.length
          ? `Contains a banned phrase from this account's voice: ${found.map((f) => `“${f}”`).join(", ")}.`
          : "Contains a banned phrase from this account's voice.";
        break;
      }
      case "pii": {
        const kinds = list(d.found)
          .map((x) => (x && typeof x === "object" ? str((x as Record<string, unknown>).kind) : undefined))
          .filter((x): x is string => !!x);
        text = kinds.length
          ? `Looks like personal data (${[...new Set(kinds)].join(", ")}). Remove it before this can go out.`
          : "Looks like personal data. Remove it before this can go out.";
        break;
      }
      case "platform_policy": {
        const issues = list(d.issues).filter((x): x is string => typeof x === "string");
        text = issues.length ? issues.join(" ") : "Breaks a platform rule.";
        break;
      }
      case "duplicate_post":
        text =
          g.score !== null
            ? `Reads like an earlier post on this account (similarity ${g.score.toFixed(2)}).`
            : "Reads like an earlier post on this account.";
        break;
      case "editor_review": {
        const n = num(d.issues) ?? 0;
        text = `The Editor flagged ${n} ${n === 1 ? "issue" : "issues"}${g.outcome === "block" ? ", at least one blocking" : ""}.`;
        break;
      }
      default:
        text = `${guardrailLabel(g.guardrail_key)}: ${g.outcome}.`;
    }
    out.push({ key: g.guardrail_key, outcome: g.outcome, text });
  }
  return out.sort((a, b) => (a.outcome === b.outcome ? 0 : a.outcome === "block" ? -1 : 1));
}

export interface CrossAccountFinding {
  outcome: "warn" | "block";
  score: number | null;
  a: Pick<Variant, "id" | "nickname">;
  b: Pick<Variant, "id" | "nickname">;
  platform: string | null;
}

/** cross_account_duplicate verdicts that name two of this set's variants. Blocks first. */
export function crossAccountFindings(set: Pick<PostSet, "set_guardrails" | "variants">): CrossAccountFinding[] {
  const byId = new Map(set.variants.map((v) => [v.id, v]));
  const out: CrossAccountFinding[] = [];
  for (const g of set.set_guardrails) {
    if (g.guardrail_key !== "cross_account_duplicate" || (g.outcome !== "warn" && g.outcome !== "block")) continue;
    const d = g.details as Record<string, unknown>;
    const [ia, ib] = list(d.variant_ids).filter((x): x is string => typeof x === "string");
    const a = ia ? byId.get(ia) : undefined;
    const b = ib ? byId.get(ib) : undefined;
    if (!a || !b) continue;
    out.push({ outcome: g.outcome, score: g.score, a, b, platform: str(d.platform) ?? null });
  }
  return out.sort((x, y) => (x.outcome === y.outcome ? 0 : x.outcome === "block" ? -1 : 1));
}

/** "X @main and X @tech read too alike (0.93) …" for the banner. */
export function crossAccountText(f: CrossAccountFinding): string {
  const where = f.platform ? ` on ${platformLabel(f.platform as ApiPlatform)}` : "";
  const score = f.score !== null ? ` (similarity ${f.score.toFixed(2)})` : "";
  return f.outcome === "block"
    ? `${f.a.nickname} and ${f.b.nickname} read almost the same${score}. Platforms suspend near-identical posts across accounts${where}, so neither can go out until one is rewritten.`
    : `${f.a.nickname} and ${f.b.nickname} are close${score}. Worth making them more distinct before approval${where ? `, since platforms penalise near-duplicates${where}` : ""}.`;
}

export interface SummaryPill {
  tone: StatusTone;
  label: string;
}

/**
 * The commit bar: "All 4 accounts distinct" and "Guardrails clear", derived
 * from the verdicts the API stored, never assumed. "Not checked yet" until
 * the checks have run.
 */
export function setSummary(set: Pick<PostSet, "set_guardrails" | "variants">): { distinct: SummaryPill; guardrails: SummaryPill } {
  const enabled = set.variants.filter((v) => v.enabled);
  const drafted = enabled.filter((v) => v.current_version);
  const cross = set.set_guardrails.filter((g) => g.guardrail_key === "cross_account_duplicate");
  const crossBlock = cross.filter((g) => g.outcome === "block").length;
  const crossWarn = cross.filter((g) => g.outcome === "warn").length;

  let distinct: SummaryPill;
  if (crossBlock) distinct = { tone: "danger", label: crossBlock === 1 ? "Two accounts too similar" : `${crossBlock} pairs too similar` };
  else if (crossWarn) distinct = { tone: "warn", label: crossWarn === 1 ? "Two accounts close" : `${crossWarn} pairs close` };
  else if (cross.length > 0 && drafted.length > 0)
    distinct = { tone: "ok", label: `All ${drafted.length} ${drafted.length === 1 ? "account" : "accounts"} distinct` };
  else if (drafted.length === 1) distinct = { tone: "neutral", label: "One account, nothing to compare" };
  else distinct = { tone: "neutral", label: "Distinctness not checked yet" };

  const reasons = drafted.map((v) => variantGuardrailReasons(v));
  const blocked = reasons.filter((r) => r.some((x) => x.outcome === "block")).length;
  const warned = reasons.filter((r) => r.length > 0 && !r.some((x) => x.outcome === "block")).length;
  const checked = drafted.filter((v) => v.guardrails.length > 0).length;

  let guardrails: SummaryPill;
  if (blocked) guardrails = { tone: "danger", label: `${blocked} ${blocked === 1 ? "account" : "accounts"} blocked` };
  else if (warned) guardrails = { tone: "warn", label: `${warned} ${warned === 1 ? "warning" : "warnings"}` };
  else if (checked > 0 && checked === drafted.length) guardrails = { tone: "ok", label: "Guardrails clear" };
  else guardrails = { tone: "neutral", label: "Guardrails not run yet" };

  return { distinct, guardrails };
}

/* ---- targets ----------------------------------------------------------- */

type GroupLike = Pick<GroupOut, "id" | "name" | "members">;
type AccountLike = Pick<AccountOut, "id" | "nickname" | "status">;

/**
 * The accounts a selection would reach, deduplicated, as the API expands it:
 * group members plus individually added accounts, active ones only. A
 * prediction for the picker; the create response is the truth.
 */
export function predictTargets(
  groupIds: readonly string[],
  accountIds: readonly string[],
  groups: readonly GroupLike[],
  accounts: readonly AccountLike[],
): string[] {
  const active = new Set(accounts.filter((a) => a.status === "active").map((a) => a.id));
  const out = new Set<string>();
  for (const g of groups) {
    if (!groupIds.includes(g.id)) continue;
    for (const m of g.members) if (active.has(m.social_account_id)) out.add(m.social_account_id);
  }
  for (const id of accountIds) if (active.has(id)) out.add(id);
  return [...out];
}

function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * composer.html: "Acme Page is also in Company Official and Product Launch —
 * adding those groups still gives it one post." For the first account in a
 * chosen group that other groups also contain; null when there is none.
 */
export function overlapNote(groupIds: readonly string[], groups: readonly GroupLike[]): string | null {
  const byAccount = new Map<string, { name: string; groups: GroupLike[] }>();
  for (const g of groups) {
    for (const m of g.members) {
      const entry = byAccount.get(m.social_account_id) ?? { name: m.nickname, groups: [] };
      entry.groups.push(g);
      byAccount.set(m.social_account_id, entry);
    }
  }
  for (const { name, groups: within } of byAccount.values()) {
    if (within.length < 2 || !within.some((g) => groupIds.includes(g.id))) continue;
    const chosen = within.filter((g) => groupIds.includes(g.id));
    const others = within.filter((g) => !groupIds.includes(g.id));
    if (others.length === 0) return `${name} is in ${joinNames(chosen.map((g) => g.name))} — it still gets one post.`;
    return `${name} is also in ${joinNames(others.map((g) => g.name))} — adding ${others.length === 1 ? "that group" : "those groups"} still gives it one post.`;
  }
  return null;
}

export const SKIP_REASON_TEXT: Readonly<Record<string, string>> = {
  excluded: "excluded",
  inactive: "not connected and active",
  unknown: "no longer in the workspace",
};

/** "Left out: Acme FB (not connected and active), Acme IG (excluded)." */
export function skippedText(skipped: readonly SkippedTarget[], accounts: readonly Pick<AccountOut, "id" | "nickname">[]): string | null {
  if (skipped.length === 0) return null;
  const names = new Map(accounts.map((a) => [a.id, a.nickname]));
  const parts = skipped.map((s) => `${names.get(s.social_account_id) ?? "An account"} (${SKIP_REASON_TEXT[s.reason] ?? s.reason})`);
  return `Left out: ${parts.join(", ")}.`;
}

/*
 * `skipped` is only in the create response. The Composer and Discovery both
 * navigate to ?set=<id> right after creating, so they leave it here for the
 * editor to show once.
 */
const SKIPPED_PREFIX = "mera:skipped:";

export function rememberSkipped(setId: string, skipped: readonly SkippedTarget[]): void {
  if (skipped.length === 0) return;
  try {
    window.sessionStorage.setItem(SKIPPED_PREFIX + setId, JSON.stringify(skipped));
  } catch {
    // Storage blocked: the notice is a courtesy, the set is fine without it.
  }
}

/** The raw JSON (a stable snapshot for useSyncExternalStore), or null. */
export function readSkippedRaw(setId: string): string | null {
  try {
    return window.sessionStorage.getItem(SKIPPED_PREFIX + setId);
  } catch {
    return null;
  }
}

export function forgetSkipped(setId: string): void {
  try {
    window.sessionStorage.removeItem(SKIPPED_PREFIX + setId);
  } catch {
    // ignore
  }
}

export function parseSkipped(raw: string | null): SkippedTarget[] {
  if (!raw) return [];
  try {
    const value = JSON.parse(raw) as unknown;
    return Array.isArray(value)
      ? value.filter(
          (x): x is SkippedTarget =>
            !!x && typeof x === "object" && typeof (x as SkippedTarget).social_account_id === "string" && typeof (x as SkippedTarget).reason === "string",
        )
      : [];
  } catch {
    return [];
  }
}

/* ---- links and copy ---------------------------------------------------- */

export function composerHref(slug: string, q: { set?: string; item?: string; template?: string } = {}): string {
  const params = new URLSearchParams();
  if (q.set) params.set("set", q.set);
  if (q.item) params.set("item", q.item);
  if (q.template) params.set("template", q.template);
  const s = params.toString();
  return `/w/${slug}/composer${s ? `?${s}` : ""}`;
}

/** composer.html quick-rewrite chips, each a Writer instruction (POST …/rewrite). */
export const REWRITE_CHIPS: readonly { label: string; instruction: string }[] = [
  { label: "Shorter", instruction: "Make it shorter. Keep the point and the link." },
  { label: "More professional", instruction: "Make it more professional in tone." },
  { label: "Add a hook", instruction: "Open with a stronger hook." },
];

export const RECENT_DRAFTS_LIMIT = 10;
export const MAX_TARGETS = 25;

/** Fields the new-post form shows inline. */
export const NEW_POST_FIELDS = ["targets", "topic_brief", "content_item_id", "title"] as const;

/** Copy for post-set API errors that carry no field (or whose field the form doesn't show). */
export const POST_SET_ERROR_TEXT: Readonly<Record<string, string>> = {
  forbidden: "You can't change this post. Editors and up draft posts, and a scoped editor only for their own groups and accounts.",
  workspace_archived: "This workspace is archived, so nothing here can change.",
  not_found: "That post set no longer exists. Reload to see the current list.",
  version_conflict: "Someone saved a newer version of this account's post while you were editing. Reload to see it, then make your change again.",
  generation_in_progress: "Drafting is already running for this post. Wait for it to finish, then try again.",
  post_set_archived: "This post set is archived, so it can't change.",
  nothing_to_revise: "There is no draft to rewrite yet. Draft it first.",
  nothing_to_tag: "There is no draft to suggest hashtags for yet. Draft it first.",
  no_subject: "Start from a content item or write a topic brief.",
  no_targets: "Pick at least one connected, active account.",
  too_many_targets: `At most ${MAX_TARGETS} accounts per post.`,
  unknown_content_item: "That content item no longer exists. Pick another or write a topic brief.",
  unknown_group: "One of those groups no longer exists. Reload the page.",
  unknown_brand_voice: "That voice no longer exists. Reload to see the current voices.",
  unknown_variant: "That account is no longer part of this post. Reload the page.",
  empty_post: "The post is empty.",
  variant_locked: "This account's post is scheduled or published, so its text can't change here.",
  post_set_locked: "This post set is past drafting, so it can't be archived.",
  invalid_link: "The link must be a public http(s) URL.",
  voice_not_applicable: "That voice belongs to another group or account. Pick a workspace voice, or one for this account or its group.",
  based_on_version_required: "This account's post changed while you were editing. Reload to see it, then make your change again.",
};

/** The 409 copy, used as is in tests too. */
export const VERSION_CONFLICT_TEXT = POST_SET_ERROR_TEXT.version_conflict!;
