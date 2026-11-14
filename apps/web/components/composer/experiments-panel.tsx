"use client";

import { abResults, createAb, setEvergreen } from "@mera/sdk";
import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Switch } from "@/components/ui/switch";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { type AbTest, OUTCOME_COPY, formatAbRate } from "@/lib/experiments";

/*
 * A/B tests and evergreen recycling for one post set (P3-M2, P3-M5). Version B is its own
 * post set for one account, approved and placed like any other; the verdict waits until
 * both have published and each has enough impressions. An evergreen post comes back as a
 * fresh draft after its cool-down, and still needs approval.
 */
export function ExperimentsPanel({
  workspace,
  set,
  variantId,
  canEdit,
}: {
  workspace: { id: string; slug: string; timezone: string };
  set: { id: string; state: string; is_evergreen?: boolean; recycle_after?: string | null; ab_parent_id?: string | null; ab_hypothesis?: string | null };
  variantId: string | null;
  canEdit: boolean;
}) {
  const id = useId();
  const path = { workspace_id: workspace.id, set_id: set.id };
  const [tests, setTests] = useState<AbTest[] | null>(null);
  const [minImpressions, setMinImpressions] = useState(500);
  const [hypothesis, setHypothesis] = useState("");
  const [body, setBody] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [evergreen, setEvergreenState] = useState(Boolean(set.is_evergreen));
  const [recycleAfter, setRecycleAfter] = useState(set.recycle_after ?? null);
  const [cooldown, setCooldown] = useState("60");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isB = Boolean(set.ab_parent_id);

  useEffect(() => {
    if (isB) return;
    let live = true;
    void settle(abResults({ client: browserApi, path })).then((r) => {
      if (!live || !r.ok) return;
      const d = r.data as { min_impressions: number; tests: AbTest[] };
      setTests(d.tests);
      setMinImpressions(d.min_impressions);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [set.id, isB, created]);

  async function startB() {
    if (!variantId) return;
    setBusy(true);
    setError(null);
    const r = await settle(
      createAb({
        client: browserApi,
        path: { ...path, variant_id: variantId },
        body: { hypothesis: hypothesis.trim(), body: body.trim() || null },
      }),
    );
    setBusy(false);
    if (!r.ok) return setError(displayMessage(r.error));
    setCreated(r.data.id);
    setHypothesis("");
    setBody("");
  }

  async function toggleEvergreen(on: boolean) {
    const days = Number(cooldown);
    if (on && !(days >= 7 && days <= 365)) return setError("A cool-down is 7 to 365 days.");
    setError(null);
    const r = await settle(setEvergreen({ client: browserApi, path, body: { evergreen: on, cooldown_days: on ? days : 60 } }));
    if (!r.ok) return setError(displayMessage(r.error));
    const d = r.data as { is_evergreen: boolean; recycle_after: string | null };
    setEvergreenState(d.is_evergreen);
    setRecycleAfter(d.recycle_after);
  }

  const published = set.state === "published" || set.state === "partially_published";

  return (
    <Panel aria-labelledby={`${id}-title`}>
      <PanelHead title={<span id={`${id}-title`}>Experiments</span>} />
      <PanelBody className="grid gap-4">
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}

        {isB ? (
          <Notice tone="info">
            This is version B of an A/B test{set.ab_hypothesis ? `: ${set.ab_hypothesis}` : ""}.{" "}
            <Link className="underline" href={`/w/${workspace.slug}/composer?set=${set.ab_parent_id}`}>
              Open version A
            </Link>
          </Notice>
        ) : (
          <section className="grid gap-2" aria-label="A/B test">
            <h3 className="text-sm font-semibold">A/B test</h3>
            {tests && tests.length > 0 ? (
              <ul className="grid gap-2">
                {tests.map((t) => (
                  <li key={t.b.post_set_id} className="grid gap-1 rounded-md border border-border p-2.5 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{t.hypothesis ?? "Version B"}</span>
                      <Tag tone={t.outcome === "a" || t.outcome === "b" ? "ok" : "neutral"}>{OUTCOME_COPY[t.outcome] ?? t.outcome}</Tag>
                    </div>
                    <p className="tabular text-fg-muted">
                      A {formatAbRate(t.a.rate, t.a.impressions, minImpressions)} · B {formatAbRate(t.b.rate, t.b.impressions, minImpressions)}
                    </p>
                    <Link className="text-caption underline" href={`/w/${workspace.slug}/composer?set=${t.b.post_set_id}`}>
                      Open version B
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-fg-muted">
                Try a different version for the selected account. Each needs {minImpressions} impressions before Mera calls a winner.
              </p>
            )}
            {created ? (
              <Notice tone="info" icon="check">
                Version B is drafted.{" "}
                <Link className="underline" href={`/w/${workspace.slug}/composer?set=${created}`}>
                  Open it
                </Link>{" "}
                to review and send it for approval.
              </Notice>
            ) : null}
            {canEdit && variantId ? (
              <div className="grid gap-2">
                <Field label="What does version B test?" hint="For example: a question as the opening line.">
                  <Input value={hypothesis} maxLength={300} onChange={(e) => setHypothesis(e.target.value)} />
                </Field>
                <Field label="Version B text (optional)" hint="Leave empty and the Writer drafts it.">
                  <Textarea rows={3} value={body} maxLength={10000} onChange={(e) => setBody(e.target.value)} />
                </Field>
                <div>
                  <Button size="sm" disabled={busy || hypothesis.trim().length < 3} onClick={() => void startB()}>
                    {busy ? "Drafting…" : "Create version B"}
                  </Button>
                </div>
              </div>
            ) : null}
          </section>
        )}

        <section className="grid gap-2" aria-label="Evergreen">
          <h3 className="text-sm font-semibold">Evergreen</h3>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2">
              <Switch
                checked={evergreen}
                disabled={!canEdit || !published}
                onCheckedChange={(on) => void toggleEvergreen(on)}
                aria-label="Bring this post back as a fresh draft"
              />
              Bring it back as a fresh draft every
            </label>
            <Input
              className="w-16"
              inputMode="numeric"
              aria-label="Cool-down in days"
              value={cooldown}
              disabled={!canEdit || evergreen}
              onChange={(e) => setCooldown(e.target.value)}
            />
            <span>days</span>
          </div>
          <p className="text-caption text-fg-muted">
            {!published
              ? "Available once the post has been published."
              : evergreen && recycleAfter
                ? <>Next refresh after <Time value={recycleAfter} tz={workspace.timezone} />. It still needs approval.</>
                : "The refresh is a new draft; it is never published without approval."}
          </p>
        </section>
      </PanelBody>
    </Panel>
  );
}
