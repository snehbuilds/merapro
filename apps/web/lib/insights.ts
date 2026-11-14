/* Insights, weights, calibration and comparison (P3-M1): shapes and formatting. */

export interface Insight {
  id: string;
  kind: string;
  title: string;
  body: string;
  confidence: number;
  period_start: string;
  period_end: string;
  acknowledged_at: string | null;
}

export interface WeightChange {
  id: string;
  subject_type: "source" | "pillar";
  subject_id: string;
  before: number;
  after: number;
  reason: string;
  reverted_at: string | null;
}

export interface Weights {
  auto_tune: boolean;
  max_step: number;
  current: { subject_type: string; subject_id: string; name: string; weight: number }[];
  changes: WeightChange[];
}

export interface Band {
  low: number;
  high: number;
  decided: number;
  approved_unedited: number;
  rate: number | null;
}

export interface Calibration {
  min_per_band: number;
  workspace: { decided: number; separates: boolean | null; bands: Band[] };
}

export interface ComparisonRow {
  key: string;
  name: string;
  posts: number;
  mean_ratio: number | null;
}

export interface Comparison {
  groups: ComparisonRow[];
  accounts: ComparisonRow[];
  platforms: ComparisonRow[];
}

export function bandLabel(b: Pick<Band, "low" | "high">): string {
  return `${Math.round(b.low * 100)}–${Math.round(b.high * 100)}%`;
}

/** A rate only once the band has enough decisions; never a misleading 0%. */
export function formatRate(rate: number | null, decided: number, min: number): string {
  if (rate === null || decided < min) return "Not enough data";
  return `${Math.round(rate * 100)}%`;
}

/** Engagement against the account's own baseline: 1.25 reads "+25% vs usual". */
export function formatRatio(ratio: number | null): string {
  if (ratio === null) return "Not enough data";
  const pct = Math.round((ratio - 1) * 100);
  if (pct === 0) return "As usual";
  return `${pct > 0 ? "+" : ""}${pct}% vs usual`;
}

export function separatesCopy(separates: boolean | null): string {
  if (separates === null) return "Not enough decisions yet to tell whether confidence predicts approval.";
  return separates
    ? "High-confidence drafts are approved as written clearly more often: confidence is meaningful here."
    : "High- and low-confidence drafts are approved about as often: do not rely on confidence yet.";
}
