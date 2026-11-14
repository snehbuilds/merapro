import { describe, expect, it } from "vitest";
import { formatDuration, formatUsd, runState } from "@/lib/agents";
import type { PostSet, Variant } from "@/lib/api/models";
import {
  applicableVoices,
  confidenceTone,
  conventionLine,
  crossAccountFindings,
  formatLift,
  isVariantEditable,
  normalizeHashtag,
  overlapNote,
  parseSkipped,
  predictTargets,
  readIssues,
  readNotes,
  setSummary,
  skippedText,
  variantGuardrailReasons,
  weightedLength,
} from "@/lib/composer";
import { groupVoicesByScope, voiceSummary } from "@/lib/voices";

const g = (id: string, name: string, ids: string[]) => ({
  id,
  name,
  members: ids.map((m, i) => ({ social_account_id: m, nickname: m.toUpperCase(), platform: "x" as const, handle: m, status: "active" as const, position: i, enabled_by_default: true })),
});

describe("composer lib", () => {
  it("counts as the platform does", () => {
    expect(weightedLength("see https://example.com/a/very/long/path/indeed ok", "x")).toBe(4 + 23 + 3);
    expect(weightedLength("👍🏽 hi", "bluesky")).toBe(4);
    expect(weightedLength("👍🏽 hi", "mastodon")).toBe(5);
    expect(conventionLine("linkedin_member")).toBe("3,000 character limit · 0–2 hashtags conventional");
    expect(conventionLine("x", 280)).toBe("280 character limit · 0–1 hashtag conventional");
  });

  it("normalises hashtags like the API", () => {
    expect(normalizeHashtag("#Postgres 18!")).toBe("postgres18");
    expect(normalizeHashtag("  ##  ")).toBeNull();
  });

  it("never shows a lift without a measured sample", () => {
    expect(formatLift({ lift_vs_account_median: null, sample_size: 0 })).toBeNull();
    expect(formatLift({ lift_vs_account_median: 1.34, sample_size: 9 })).toBeNull(); // under 10 posts
    expect(formatLift({ lift_vs_account_median: 1.34, sample_size: 41 })).toBe("+34%");
    expect(formatLift({ lift_vs_account_median: 0.9, sample_size: 12 })).toBe("−10%");
    expect(formatLift({ lift_vs_account_median: 1, sample_size: 12 })).toBe("+0%");
  });

  it("tones confidence", () => {
    expect([0.84, 0.7, 0.4].map(confidenceTone)).toEqual(["ok", "warn", "danger"]);
  });

  it("predicts deduplicated, active targets and explains overlap", () => {
    const groups = [g("g1", "Tech", ["a", "b"]), g("g2", "Corp", ["b"]), g("g3", "Launch", ["b", "c"])];
    const accounts = ["a", "b", "c", "d"].map((id) => ({ id, nickname: id, status: id === "c" ? ("needs_reauth" as const) : ("active" as const) }));
    expect(predictTargets(["g1", "g3"], ["a", "d"], groups, accounts).sort()).toEqual(["a", "b", "d"]);
    expect(overlapNote(["g1"], groups)).toBe("B is also in Corp and Launch — adding those groups still gives it one post.");
    expect(overlapNote(["g1", "g2", "g3"], groups)).toBe("B is in Tech, Corp and Launch — it still gets one post.");
    expect(overlapNote([], groups)).toBeNull();
  });

  it("describes skipped targets and tolerates junk storage", () => {
    expect(skippedText([{ social_account_id: "a", reason: "inactive" }, { social_account_id: "z", reason: "excluded" }], [{ id: "a", nickname: "Acme FB" }])).toBe(
      "Left out: Acme FB (not connected and active), An account (excluded).",
    );
    expect(skippedText([], [])).toBeNull();
    expect(parseSkipped("{not json")).toEqual([]);
    expect(parseSkipped('[{"social_account_id":"a","reason":"x"},{"nope":1}]')).toEqual([{ social_account_id: "a", reason: "x" }]);
  });

  it("reads agent notes and issues defensively", () => {
    expect(readNotes({ agent_notes: { angle: "A", voice_match: "high", differentiate_attempts: 2 } })).toMatchObject({
      angle: "A",
      voiceMatch: undefined,
      differentiateAttempts: 2,
    });
    expect(readIssues({ compliance_flags: [{ category: "tone", severity: "block" }, { severity: "warning" }] })).toEqual([
      { category: "tone", severity: "block", excerpt: undefined, explanation: undefined, suggestedFix: undefined },
    ]);
  });

  const v = (id: string, over: Partial<Variant> = {}) =>
    ({ id, nickname: id, enabled: true, state: "drafting", guardrails: [], current_version: { id: `ver-${id}` }, ...over }) as unknown as Variant;

  it("words guardrail verdicts, blocks first", () => {
    const reasons = variantGuardrailReasons({
      guardrails: [
        { guardrail_key: "platform_policy", outcome: "warn", score: null, details: { issues: ["4 hashtags; 2 is the convention here."] } },
        { guardrail_key: "pii", outcome: "block", score: null, details: { found: [{ kind: "email", excerpt: "a***" }] } },
        { guardrail_key: "banned_words", outcome: "pass", score: null, details: {} },
      ],
    });
    expect(reasons.map((r) => r.outcome)).toEqual(["block", "warn"]);
    expect(reasons[0]!.text).toBe("Looks like personal data (email). Remove it before this can go out.");
  });

  it("summarises the set only from stored verdicts", () => {
    const set = (over: Partial<PostSet>) => ({ variants: [v("a"), v("b")], set_guardrails: [], ...over }) as unknown as PostSet;
    expect(setSummary(set({})).distinct.label).toBe("Distinctness not checked yet");
    expect(setSummary(set({ set_guardrails: [{ guardrail_key: "cross_account_duplicate", outcome: "pass", score: 0.4, details: {} }] })).distinct.label).toBe(
      "All 2 accounts distinct",
    );
    const blocked = set({
      set_guardrails: [{ guardrail_key: "cross_account_duplicate", outcome: "block", score: 0.95, details: { variant_ids: ["a", "b"], platform: "x" } }],
    });
    expect(setSummary(blocked).distinct).toEqual({ tone: "danger", label: "Two accounts too similar" });
    expect(crossAccountFindings(blocked)[0]).toMatchObject({ outcome: "block", a: { id: "a" }, b: { id: "b" }, platform: "x" });
    // A finding naming a variant that isn't in the set (scoped view) is dropped, not guessed.
    expect(crossAccountFindings(set({ set_guardrails: [{ guardrail_key: "cross_account_duplicate", outcome: "block", score: 0.95, details: { variant_ids: ["a", "zz"] } }] }))).toEqual([]);
  });

  it("locks variants past drafting and filters voices to the ones that apply", () => {
    // Editing an account in or after approval makes a new version (ADR-0014); only scheduled and later are locked.
    expect(["drafting", "blocked", "skipped", "approved", "scheduled", "published"].map((state) => isVariantEditable({ state }))).toEqual([true, true, true, true, false, false]);
    const voices = [
      { id: "w", scope_type: "workspace", scope_id: null },
      { id: "g", scope_type: "account_group", scope_id: "g1" },
      { id: "g2", scope_type: "account_group", scope_id: "g2" },
      { id: "a", scope_type: "social_account", scope_id: "acc" },
    ];
    expect(applicableVoices(voices, { via_account_group_id: "g1", social_account_id: "acc" }).map((x) => x.id)).toEqual(["w", "g", "a"]);
    expect(applicableVoices(voices, { via_account_group_id: null, social_account_id: "other" }).map((x) => x.id)).toEqual(["w"]);
  });
});

describe("agents lib", () => {
  it("formats money without hiding real costs", () => {
    expect(formatUsd("0")).toBe("$0");
    expect(formatUsd("0.16230000")).toBe("$0.1623");
    expect(formatUsd("18.4")).toBe("$18.40");
    expect(formatUsd("0.00001")).toBe("<$0.0001");
    expect(formatUsd("nope")).toBe("—");
  });

  it("formats durations and run states", () => {
    expect(formatDuration(812)).toBe("812ms");
    expect(formatDuration(38123)).toBe("38.1s");
    expect(formatDuration(125000)).toBe("2m 5s");
    expect(runState("awaiting_human").label).toBe("Paused");
    expect(runState("something_new").label).toBe("something new");
  });
});

describe("voices lib", () => {
  it("groups by scope and summarises", () => {
    const voices = [
      { scope_type: "social_account" as const, scope_name: "Work LinkedIn", name: "P", is_default: false, tone: "Direct. Short." },
      { scope_type: "workspace" as const, scope_name: "Acme", name: "C", is_default: true, tone: null },
    ];
    expect(groupVoicesByScope(voices).map((x) => x.scope)).toEqual(["workspace", "social_account"]);
    expect(voiceSummary(voices[0]!)).toBe("Work LinkedIn · Direct");
    expect(voiceSummary(voices[1]!)).toBe("Default");
  });
});
