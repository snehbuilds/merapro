import type {
  AgentOut,
  ApprovalRequestOut,
  ApprovalVariantOut,
  AutomationOut,
  DecisionOut,
  ContentItemOut,
  CostReportOut,
  CostRowOut,
  GuardrailOut,
  PostSetOut,
  RunOut,
  SkippedOut,
  StepOut,
  TagOut,
  VariantOut,
  VersionOut,
  VoiceOut,
  VoiceSampleOut,
  VoiceScope,
  ContentStatsOut,
  DuplicateRefOut,
  FeedTestOut,
  GroupMemberOut,
  MemberOut,
  PillarOut,
  SourceHealthOut,
  SourceOut,
  SourceType,
} from "@mera/sdk";

/*
 * App-facing names for generated types. Import these rather than the SDK names, so a
 * backend rename touches only this file.
 */

/** A workspace member (GET /workspaces/{id}/members). */
export type WorkspaceMember = MemberOut;

/** A social account's membership in an account group (GroupOut.members). */
export type GroupMember = GroupMemberOut;

/** A content source (GET /workspaces/{id}/sources). group_ids empty = feeds every group. */
export type Source = SourceOut;

export type SourceKind = SourceType;

/** GET /sources/{id}/health. */
export type SourceHealth = SourceHealthOut;

/** POST /sources/test: a dry run of a feed URL; never saves. */
export type FeedTest = FeedTestOut;

/** A ranked article (GET /content-items/trending). */
export type ContentItem = ContentItemOut;

/** A near-duplicate collapsed into a ContentItem. */
export type DuplicateRef = DuplicateRefOut;

/** GET /content-items/stats. drafted / published are null until M5 / M8. */
export type ContentStats = ContentStatsOut;

/** A content pillar (GET /content-pillars). account_group_id null = workspace-wide. */
export type Pillar = PillarOut;

/* ---- M5: post sets, brand voices, agents ---- */

/** A post set: one story, one variant per target account (GET /post-sets/{id}). */
export type PostSet = PostSetOut;

/** One account's post inside a set. */
export type Variant = VariantOut;

/** An immutable version of a variant's content. */
export type Version = VersionOut;

/** A guardrail verdict on a variant or (cross_account_duplicate) on the whole set. */
export type Guardrail = GuardrailOut;

/** An account left out of a set at creation, and why (excluded, inactive, unknown). */
export type SkippedTarget = SkippedOut;

/** A hashtag suggestion with its evidence (POST …/hashtags/suggest). */
export type HashtagSuggestion = TagOut;

/** A brand voice profile at one of three scopes. */
export type Voice = VoiceOut;

export type VoiceScopeType = VoiceScope;

/** POST /brand-voices/{id}/sample. */
export type VoiceSample = VoiceSampleOut;

/** An agent and its model routing (GET /agents). */
export type Agent = AgentOut;

/** An approval request (GET /approval-requests): one post set, decided per account. */
export type ApprovalRequest = ApprovalRequestOut;

/** One account's approval state inside a request, with the exact text it covers. */
export type ApprovalVariant = ApprovalVariantOut;

export type ApprovalDecision = DecisionOut;

/** Kill switch and autopilot at one scope (GET /automation). */
export type Automation = AutomationOut;

/** An agent run (GET /agent-runs, /agent-runs/{id}). */
export type AgentRun = RunOut;

export type AgentStep = StepOut;

/** GET /agent-runs/costs. */
export type CostReport = CostReportOut;

export type CostRow = CostRowOut;
