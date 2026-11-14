/* A/B tests (P3-M2): the result shape and how a result reads. */

export interface AbSide {
  post_set_id: string;
  published: boolean;
  impressions: number | null;
  engagement: number | null;
  rate: number | null;
}

export interface AbTest {
  social_account_id: string;
  hypothesis: string | null;
  a: AbSide;
  b: AbSide;
  outcome: "too_early" | "no_difference" | "a" | "b" | "not_reported";
  p_value: number | null;
}

export const OUTCOME_COPY: Record<string, string> = {
  too_early: "Too early",
  no_difference: "No clear difference",
  a: "A did better",
  b: "B did better",
  not_reported: "Not reported",
};

/** Engagement per impression, once a side has enough impressions to mean anything. */
export function formatAbRate(rate: number | null, impressions: number | null, min: number): string {
  if (impressions === null) return "not reported";
  if (impressions < min || rate === null) return `${impressions} of ${min} impressions`;
  return `${(rate * 100).toFixed(1)}%`;
}
