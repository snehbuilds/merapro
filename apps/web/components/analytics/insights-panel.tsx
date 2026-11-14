"use client";

import {
  acknowledgeInsight,
  analyticsSettings,
  comparison,
  confidenceCalibration,
  listInsights,
  revertWeight,
  weights,
} from "@mera/sdk";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Switch } from "@/components/ui/switch";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import {
  type Calibration,
  type Comparison,
  type Insight,
  type Weights,
  bandLabel,
  formatRatio,
  formatRate,
  separatesCopy,
} from "@/lib/insights";

/*
 * What the weekly insights found, how the feedback loop moved source and pillar weights
 * (each move revertible), whether the Editor's confidence means anything yet, and groups,
 * accounts and platforms side by side (P3-M1.3 to M1.7). Every number comes from Mera's
 * own data; "Not enough data" where it is too thin to say.
 */
export function InsightsPanels({ workspaceId, timezone, canTune }: { workspaceId: string; timezone: string; canTune: boolean }) {
  const path = { workspace_id: workspaceId };
  const [insights, setInsights] = useState<Insight[] | null>(null);
  const [w, setW] = useState<Weights | null>(null);
  const [cal, setCal] = useState<Calibration | null>(null);
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [i, wt, c, k] = await Promise.all([
      settle(listInsights({ client: browserApi, path, query: { limit: 10 } })),
      settle(weights({ client: browserApi, path })),
      settle(confidenceCalibration({ client: browserApi, path })),
      settle(comparison({ client: browserApi, path, query: { days: 30 } })),
    ]);
    if (i.ok) setInsights((i.data as { data: Insight[] }).data);
    if (wt.ok) setW(wt.data as unknown as Weights);
    if (c.ok) setCal(c.data as unknown as Calibration);
    if (k.ok) setCmp(k.data as unknown as Comparison);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads after mount; state is set after the await
    void load();
  }, [load]);

  async function ack(id: string) {
    const r = await settle(acknowledgeInsight({ client: browserApi, path: { ...path, insight_id: id } }));
    if (!r.ok) return setError(displayMessage(r.error));
    setInsights((xs) => (xs ?? []).map((x) => (x.id === id ? { ...x, acknowledged_at: new Date().toISOString() } : x)));
  }

  async function revert(id: string) {
    const r = await settle(revertWeight({ client: browserApi, path: { ...path, change_id: id } }));
    if (!r.ok) return setError(displayMessage(r.error));
    await load();
  }

  async function tune(on: boolean) {
    const r = await settle(analyticsSettings({ client: browserApi, path, body: { auto_tune: on } }));
    if (!r.ok) return setError(displayMessage(r.error));
    setW((x) => (x ? { ...x, auto_tune: on } : x));
  }

  const names = new Map((w?.current ?? []).map((c) => [c.subject_id, c.name]));

  return (
    <>
      {error ? (
        <Notice tone="danger" role="alert">
          {error}
        </Notice>
      ) : null}

      <Panel>
        <PanelHead title="Insights" />
        <PanelBody className="grid gap-3">
          {insights === null ? (
            <p className="text-sm text-fg-muted">Loading…</p>
          ) : insights.length === 0 ? (
            <p className="text-sm text-fg-muted">
              Nothing yet. Insights arrive weekly once accounts have at least 10 measured posts.
            </p>
          ) : (
            <ul className="grid gap-2">
              {insights.map((x) => (
                <li key={x.id} className="grid gap-1 rounded-md border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{x.title}</span>
                    <Tag tone="outline">{Math.round(x.confidence * 100)}% confidence</Tag>
                    {x.acknowledged_at ? <Tag tone="ok">Seen</Tag> : null}
                  </div>
                  <p className="text-sm text-fg-muted">{x.body}</p>
                  <div className="flex items-center gap-2 text-caption text-fg-subtle">
                    <span>
                      <Time value={x.period_start} tz={timezone} /> to <Time value={x.period_end} tz={timezone} />
                    </span>
                    {!x.acknowledged_at ? (
                      <Button size="sm" variant="ghost" onClick={() => void ack(x.id)}>
                        Mark as seen
                      </Button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHead
          title="Weights"
          actions={
            w && canTune ? (
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={w.auto_tune} onCheckedChange={(on) => void tune(on)} aria-label="Let insights move weights" />
                Let insights move weights
              </label>
            ) : null
          }
        />
        <PanelBody className="grid gap-3">
          <p className="text-sm text-fg-muted">
            Each week, a source or pillar whose posts do clearly better or worse moves by at most{" "}
            {Math.round((w?.max_step ?? 0.15) * 100)}%. Every move can be put back.
          </p>
          {w && w.changes.length > 0 ? (
            <ul className="divide-y divide-border">
              {w.changes.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <span className="font-semibold">{names.get(c.subject_id) ?? c.subject_type}</span>
                  <Tag tone="outline">
                    {c.before.toFixed(2)} → {c.after.toFixed(2)}
                  </Tag>
                  <span className="text-fg-muted">{c.reason}</span>
                  {c.reverted_at ? (
                    <Tag>Reverted</Tag>
                  ) : canTune ? (
                    <Button size="sm" variant="ghost" onClick={() => void revert(c.id)}>
                      Revert
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-fg-muted">No moves yet.</p>
          )}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHead title="Editor confidence" />
        <PanelBody className="grid gap-3">
          {cal ? (
            <>
              <p className="text-sm">{separatesCopy(cal.workspace.separates)}</p>
              <table className="w-full text-sm">
                <thead className="text-left text-caption text-fg-subtle">
                  <tr>
                    <th className="py-1 font-normal">Confidence</th>
                    <th className="py-1 font-normal">Decided</th>
                    <th className="py-1 font-normal">Approved as written</th>
                  </tr>
                </thead>
                <tbody className="tabular">
                  {cal.workspace.bands.map((b) => (
                    <tr key={b.low} className="border-t border-border">
                      <td className="py-1">{bandLabel(b)}</td>
                      <td className="py-1">{b.decided}</td>
                      <td className="py-1">{formatRate(b.rate, b.decided, cal.min_per_band)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="text-sm text-fg-muted">Loading…</p>
          )}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHead title="Side by side" />
        <PanelBody className="grid gap-4">
          {cmp ? (
            (["groups", "accounts", "platforms"] as const).map((dim) => (
              <div key={dim} className="grid gap-1">
                <h3 className="text-caption font-semibold uppercase text-fg-subtle">{dim}</h3>
                {cmp[dim].length === 0 ? (
                  <p className="text-sm text-fg-muted">No published posts in the last 30 days.</p>
                ) : (
                  <ul className="divide-y divide-border text-sm">
                    {cmp[dim].map((r) => (
                      <li key={r.key} className="flex items-center gap-2 py-1">
                        <span className="flex-1">{r.name}</span>
                        <span className="tabular text-fg-muted">{r.posts} posts</span>
                        <Tag tone="outline">{formatRatio(r.mean_ratio)}</Tag>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))
          ) : (
            <p className="text-sm text-fg-muted">Loading…</p>
          )}
        </PanelBody>
      </Panel>
    </>
  );
}
