"use client";

import { abortAgentRun, getAgentRun, getPostSet, resumeOrchestration, retryAgentRun, setAgentModel } from "@mera/sdk";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { Timeline, type TimelineItem } from "@/components/mera/timeline";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import {
  AGENT_ERROR_TEXT,
  GUARDRAILS_LATER,
  GUARDRAIL_THRESHOLDS,
  byCost,
  formatDuration,
  formatTokens,
  formatUsd,
  runDuration,
  runState,
  stepTone,
  stepTypeLabel,
} from "@/lib/agents";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage, type ApiError } from "@/lib/api/errors";
import type { Agent, AgentRun, AgentStep, CostReport, CostRow } from "@/lib/api/models";
import { composerHref } from "@/lib/composer";
import { SCREENS } from "@/lib/nav";
import { cn } from "@/lib/utils";

/*
 * Agents (agents.html): one run's steps with the account each served, the
 * model routing per agent (Admins choose each agent's model since Sprint 8),
 * spend by agent / model / workflow / account, and the guardrail thresholds.
 * Retry and abort are later.
 */

export interface AgentsScreenProps {
  workspace: { id: string; slug: string; name: string; timezone: string; archived: boolean };
  /** null when a list couldn't be loaded. */
  agents: Agent[] | null;
  runs: AgentRun[] | null;
  /** The run shown first, with its steps (?run=<id>, else the latest). */
  initialRun: AgentRun | null;
  costs: CostReport | null;
  /**
   * The member is scoped to some groups or accounts: runs and costs span
   * every account, so the API refuses them (403) and agent spend reads 0.
   */
  scoped?: boolean;
  /** Admins choose each agent's model for this workspace. */
  canManage?: boolean;
}

function errorText(error: ApiError): string {
  return AGENT_ERROR_TEXT[error.code] ?? displayMessage(error);
}

/** "Run 0193A4F2": a short handle for a run id. */
export function shortRunId(id: string): string {
  return id.replace(/-/g, "").slice(0, 8).toUpperCase();
}

function RunStatus({ state }: { state: string }) {
  const s = runState(state);
  return <StatusPill tone={s.tone} shape={s.shape} label={s.label} />;
}

function PanelTitle({ id, children }: { id: string; children: string }) {
  return (
    <h2 id={id} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
      {children}
    </h2>
  );
}

const SCOPED_TEXT =
  "Runs and costs span every account in the workspace, so they're shown to members with access to all of it. Your access is limited to some groups or accounts.";

export function AgentsScreen({ workspace, agents, runs, initialRun, costs, scoped = false, canManage = false }: AgentsScreenProps) {
  const [selectedId, setSelectedId] = useState<string | undefined>(initialRun?.id ?? runs?.[0]?.id);
  const [detail, setDetail] = useState<AgentRun | null>(initialRun);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  const id = useId();

  async function select(runId: string) {
    setSelectedId(runId);
    setError(null);
    const req = ++request.current;
    setLoading(true);
    const r = await settle(getAgentRun({ client: browserApi, path: { workspace_id: workspace.id, run_id: runId } }));
    if (req !== request.current) return;
    setLoading(false);
    if (!r.ok) {
      setError(errorText(r.error));
      return;
    }
    setDetail(r.data);
    window.history.replaceState(null, "", `${window.location.pathname}?run=${encodeURIComponent(runId)}`);
  }

  const sub = [
    agents ? `${agents.length} ${agents.length === 1 ? "agent" : "agents"}` : null,
    costs && !scoped ? `${formatUsd(costs.total_usd)} in 30 days` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      <Topbar title={SCREENS.agents.title} sub={sub || undefined} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div className="grid grid-cols-[minmax(0,1fr)_320px] items-start gap-3.5 max-wide:grid-cols-1">
          <div className="grid min-w-0 gap-3.5">
            {error ? (
              <Notice tone="danger" role="alert">
                Couldn&apos;t load that run. {error}
              </Notice>
            ) : null}
            {scoped ? (
              <Panel>
                <EmptyState icon="agents" title="Runs aren't shown at your scope" description={SCOPED_TEXT} />
              </Panel>
            ) : detail ? (
              <RunDetail key={detail.id} workspace={workspace} run={detail} loading={loading} canControl={canManage && !workspace.archived} />
            ) : (
              <Panel>
                <EmptyState
                  icon="agents"
                  title={runs === null ? "Couldn't load runs" : "No runs yet"}
                  description={
                    runs === null
                      ? "Reload to try again."
                      : "Runs appear here as agents work: drafting a post set, testing a voice, enriching items."
                  }
                />
              </Panel>
            )}
            <AgentTable agents={agents} showCost={!scoped} workspaceId={workspace.id} canManage={canManage && !workspace.archived} />
          </div>

          <div className="grid min-w-0 gap-3.5">
            <CostPanel costs={costs} tz={workspace.timezone} scoped={scoped} />
            <Panel aria-labelledby={`${id}-runs`}>
              <PanelHead>
                <PanelTitle id={`${id}-runs`}>Recent runs</PanelTitle>
              </PanelHead>
              {scoped ? (
                <PanelBody>
                  <p className="text-sm text-fg-muted">Not shown at your scope.</p>
                </PanelBody>
              ) : runs === null ? (
                <PanelBody>
                  <p className="text-sm text-fg-muted">Couldn&apos;t load recent runs. Reload to try again.</p>
                </PanelBody>
              ) : runs.length === 0 ? (
                <PanelBody>
                  <p className="text-sm text-fg-muted">No runs yet.</p>
                </PanelBody>
              ) : (
                <ul aria-labelledby={`${id}-runs`}>
                  {runs.map((r) => {
                    const ms = runDuration(r);
                    return (
                      <li key={r.id}>
                        <button
                          type="button"
                          aria-current={r.id === selectedId ? "true" : undefined}
                          onClick={() => void select(r.id)}
                          className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 border-b border-border px-3.5 py-2.5 text-left hover:bg-surface-hover aria-[current=true]:bg-accent-soft"
                        >
                          <span className="min-w-0">
                            <span className="block truncate font-mono text-xs">{r.workflow_key}</span>
                            <span className="block truncate text-xs text-fg-subtle">
                              {r.step_count} {r.step_count === 1 ? "step" : "steps"} · {formatUsd(r.cost_usd)}
                              {ms !== null ? ` · ${formatDuration(ms)}` : ""} ·{" "}
                              <Time value={r.started_at} tz={workspace.timezone} format="datetime" className="text-xs" />
                            </span>
                          </span>
                          <RunStatus state={r.state} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Panel>
            <GuardrailsPanel />
          </div>
        </div>
      </main>
    </>
  );
}

/** Variant id → account nickname, from the run's post set (when it drafted one). */
function useVariantNames(workspaceId: string, run: AgentRun): Map<string, string> {
  const [names, setNames] = useState<Map<string, string>>(new Map());
  const setId = run.subject_type === "post_set" ? run.subject_id : null;
  const needs = !!setId && run.steps.some((s) => s.post_variant_id);
  useEffect(() => {
    if (!needs || !setId) return;
    let live = true;
    settle(getPostSet({ client: browserApi, path: { workspace_id: workspaceId, set_id: setId } })).then((r) => {
      if (!live || !r.ok) return;
      setNames(new Map(r.data.variants.map((v) => [v.id, v.nickname])));
    });
    return () => {
      live = false;
    };
  }, [needs, setId, workspaceId]);
  return names;
}

function stepDetail(step: AgentStep, names: Map<string, string>): string {
  const parts: string[] = [];
  if (step.post_variant_id) parts.push(`→ ${names.get(step.post_variant_id) ?? `account ${step.post_variant_id.slice(0, 8)}`}`);
  parts.push(step.model ? `${step.model} · ${stepTypeLabel(step.step_type)}` : stepTypeLabel(step.step_type));
  if (step.fallback_from) parts.push(`fell back from ${step.fallback_from}`);
  if (step.tokens_in || step.tokens_out) parts.push(`${formatTokens(step.tokens_in)} in / ${formatTokens(step.tokens_out)} out`);
  parts.push(formatDuration(step.latency_ms));
  if (step.state !== "succeeded") parts.push(step.state.replace(/_/g, " "));
  if (step.error) parts.push(step.error);
  return parts.join(" · ");
}

/* P2-M1.7: stop a queued or running run (the account in progress finishes first),
   or queue a failed or stopped drafting run again. */
function RunControls({ workspace, run }: { workspace: AgentsScreenProps["workspace"]; run: AgentRun }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const active = run.state === "queued" || run.state === "running";
  const stopped = run.state === "failed" || run.state === "aborted";
  const orchestration = run.workflow_key === "orchestration";
  const retryable = stopped && (run.workflow_key === "draft_from_content" || orchestration);
  if (!active && !retryable) return null;

  async function act() {
    setBusy(true);
    setMessage(null);
    const path = { workspace_id: workspace.id, run_id: run.id };
    const r = active
      ? await settle(abortAgentRun({ client: browserApi, path }))
      : orchestration
        ? await settle(resumeOrchestration({ client: browserApi, path }))
        : await settle(retryAgentRun({ client: browserApi, path }));
    setBusy(false);
    if (!r.ok) {
      setMessage(r.error.message);
      return;
    }
    setMessage(active ? "Stopping after the current account." : orchestration ? "Resuming from the first unfinished stage." : "Queued again.");
    router.refresh();
  }

  return (
    <span className="flex items-center gap-2">
      <Button size="sm" variant={active ? "ghost" : "primary"} disabled={busy} onClick={() => void act()}>
        {busy ? (active ? "Stopping…" : "Queuing…") : active ? "Stop run" : orchestration ? "Resume" : "Retry"}
      </Button>
      {message ? (
        <span role="status" className="text-caption text-fg-muted">
          {message}
        </span>
      ) : null}
    </span>
  );
}

function RunDetail({
  workspace,
  run,
  loading,
  canControl = false,
}: {
  workspace: AgentsScreenProps["workspace"];
  run: AgentRun;
  loading: boolean;
  canControl?: boolean;
}) {
  const names = useVariantNames(workspace.id, run);
  const id = useId();
  const ms = runDuration(run);
  const steps = [...run.steps].sort((a, b) => a.seq - b.seq);
  const items: TimelineItem[] = steps.map((s) => ({
    id: s.id,
    kind: <span className="text-sm">{s.agent_key}</span>,
    detail: <span title={stepDetail(s, names)}>{stepDetail(s, names)}</span>,
    meta: <span className="text-xs">{formatUsd(s.cost_usd)}</span>,
    tone: stepTone(s),
  }));
  return (
    <Panel aria-labelledby={`${id}-title`} aria-busy={loading} className={cn(loading && "opacity-60")}>
      <PanelHead className="flex-wrap">
        <h2 id={`${id}-title`} className="text-md font-semibold">
          Run <span className="font-mono">{shortRunId(run.id)}</span>
        </h2>
        <Tag tone="outline">{run.workflow_key}</Tag>
        <span className="flex-1" />
        <RunStatus state={run.state} />
        {canControl ? <RunControls workspace={workspace} run={run} /> : null}
        {ms !== null ? <span className="tabular text-caption text-fg-subtle">{formatDuration(ms)}</span> : null}
        <span className="font-mono text-caption">{formatUsd(run.cost_usd)}</span>
      </PanelHead>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-border px-3.5 py-2.5 text-caption text-fg-muted">
        <span>Started</span>
        <Time value={run.started_at} tz={workspace.timezone} format="datetime" />
        <span aria-hidden="true">·</span>
        <span>{run.trigger.replace(/_/g, " ")}</span>
        <span aria-hidden="true">·</span>
        <span className="tabular">
          {formatTokens(run.tokens_in)} tokens in / {formatTokens(run.tokens_out)} out
        </span>
        {run.subject_type === "post_set" && run.subject_id ? (
          <>
            <span aria-hidden="true">·</span>
            <Link href={composerHref(workspace.slug, { set: run.subject_id })} className="font-medium text-accent hover:underline">
              Open the post set
            </Link>
          </>
        ) : null}
      </div>
      {run.error ? (
        <Notice tone="danger" className="m-3.5">
          {run.error}
        </Notice>
      ) : null}
      {loading ? (
        <SkeletonRows rows={3} label="Loading run" />
      ) : steps.length === 0 ? (
        <PanelBody>
          <p className="text-sm text-fg-muted">No steps recorded{run.state === "queued" ? " yet — the run is queued" : ""}.</p>
        </PanelBody>
      ) : (
        <Timeline items={items} aria-label={`Steps of run ${shortRunId(run.id)}`} />
      )}
      {run.children && run.children.length > 0 ? <RunGraph runs={run.children} names={names} /> : null}
      <PanelFoot className="grid gap-1">
        <p>
          Every step records which account it served, so cost and failure are both attributable per target. A fallback model is
          labelled, because otherwise that account&apos;s post reading differently would look like a mystery.
        </p>
      </PanelFoot>
    </Panel>
  );
}

/* P2-M7.4: an orchestration's children (drafting, media), each with its own steps and
   cost, in the order they ran. */
function RunGraph({ runs, names }: { runs: AgentRun[]; names: Map<string, string> }) {
  const total = runs.reduce((sum, r) => sum + Number(r.cost_usd), 0);
  return (
    <section aria-label="Run graph" className="border-t border-border px-3.5 py-3">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
        Run graph <span className="font-mono text-caption font-normal text-fg-subtle">{formatUsd(String(total))} across {runs.length} runs</span>
      </h3>
      <ol className="grid gap-2">
        {runs.map((r) => (
          <li key={r.id} className="rounded-md border border-border p-2.5">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Tag tone="outline">{r.workflow_key}</Tag>
              <RunStatus state={r.state} />
              <span className="flex-1" />
              <span className="font-mono text-caption">{formatUsd(r.cost_usd)}</span>
            </div>
            {r.steps.length > 0 ? (
              <ul className="mt-1.5 grid gap-0.5 text-caption text-fg-muted">
                {[...r.steps]
                  .sort((a, b) => a.seq - b.seq)
                  .map((st) => (
                    <li key={st.id} className="flex gap-2">
                      <span className="min-w-24 font-medium text-fg">{st.agent_key}</span>
                      <span className="min-w-0 flex-1 truncate" title={stepDetail(st, names)}>
                        {stepDetail(st, names)}
                      </span>
                      <span className="font-mono">{formatUsd(st.cost_usd)}</span>
                    </li>
                  ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function ModelSelector({
  agent,
  disabled,
  onChoose,
}: {
  agent: Agent;
  disabled: boolean;
  onChoose: (agent: Agent, model: string) => Promise<void>;
}) {
  const [customMode, setCustomMode] = useState(false);
  const [customValue, setCustomValue] = useState("");

  const choices = Array.from(new Set([agent.model, ...agent.choices]));
  const gemini = choices.filter((c) => c.startsWith("gemini/"));
  const openai = choices.filter((c) => c.startsWith("openai/"));
  const anthropic = choices.filter((c) => c.startsWith("anthropic/"));
  const openrouter = choices.filter((c) => c.startsWith("openrouter/"));
  const other = choices.filter(
    (c) =>
      !c.startsWith("gemini/") &&
      !c.startsWith("openai/") &&
      !c.startsWith("anthropic/") &&
      !c.startsWith("openrouter/"),
  );

  if (customMode) {
    return (
      <div className="flex items-center gap-1.5">
        <input
          type="text"
          placeholder="provider/model"
          value={customValue}
          disabled={disabled}
          onChange={(e) => setCustomValue(e.target.value)}
          className="h-7 w-48 rounded-sm border border-border-strong bg-surface px-1.5 font-mono text-xs"
        />
        <Button
          size="sm"
          disabled={disabled || !customValue.trim()}
          onClick={() => {
            void onChoose(agent, customValue.trim());
            setCustomMode(false);
          }}
        >
          Save
        </Button>
        <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setCustomMode(false)}>
          ✕
        </Button>
      </div>
    );
  }

  return (
    <select
      aria-label={`Model for ${agent.name}`}
      value={agent.model}
      disabled={disabled}
      onChange={(e) => {
        if (e.target.value === "__custom__") {
          setCustomValue("");
          setCustomMode(true);
        } else {
          void onChoose(agent, e.target.value);
        }
      }}
      className="h-7 max-w-full rounded-sm border border-border-strong bg-surface px-1.5 font-mono text-xs"
    >
      {gemini.length > 0 ? (
        <optgroup label="Google Gemini">
          {gemini.map((m) => (
            <option key={m} value={m}>
              {m}
              {m === agent.default_model ? " (default)" : ""}
            </option>
          ))}
        </optgroup>
      ) : null}
      {anthropic.length > 0 ? (
        <optgroup label="Anthropic (Claude)">
          {anthropic.map((m) => (
            <option key={m} value={m}>
              {m}
              {m === agent.default_model ? " (default)" : ""}
            </option>
          ))}
        </optgroup>
      ) : null}
      {openai.length > 0 ? (
        <optgroup label="OpenAI">
          {openai.map((m) => (
            <option key={m} value={m}>
              {m}
              {m === agent.default_model ? " (default)" : ""}
            </option>
          ))}
        </optgroup>
      ) : null}
      {openrouter.length > 0 ? (
        <optgroup label="OpenRouter">
          {openrouter.map((m) => (
            <option key={m} value={m}>
              {m}
              {m === agent.default_model ? " (default)" : ""}
            </option>
          ))}
        </optgroup>
      ) : null}
      {other.length > 0 ? (
        <optgroup label="Other Models">
          {other.map((m) => (
            <option key={m} value={m}>
              {m}
              {m === agent.default_model ? " (default)" : ""}
            </option>
          ))}
        </optgroup>
      ) : null}
      <option value="__custom__">Custom model string...</option>
    </select>
  );
}

function AgentTable({
  agents: fromServer,
  showCost,
  workspaceId,
  canManage,
}: {
  agents: Agent[] | null;
  showCost: boolean;
  workspaceId: string;
  canManage: boolean;
}) {
  const id = useId();
  const [agents, setAgents] = useState(fromServer);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editable = canManage && (agents?.some((a) => a.editable) ?? false);

  async function choose(a: Agent, model: string) {
    setSaving(a.agent_key);
    setError(null);
    const r = await settle(
      setAgentModel({
        client: browserApi,
        path: { workspace_id: workspaceId, agent_key: a.agent_key },
        body: { model: model === a.default_model ? null : model, fallbacks: [] },
      }),
    );
    setSaving(null);
    if (!r.ok) {
      setError(errorText(r.error));
      return;
    }
    setAgents((list) => list?.map((x) => (x.agent_key === a.agent_key ? r.data : x)) ?? list);
  }
  return (
    <Panel aria-labelledby={`${id}-config`}>
      <PanelHead>
        <PanelTitle id={`${id}-config`}>Agent configuration</PanelTitle>
      </PanelHead>
      {agents === null ? (
        <PanelBody>
          <p className="text-sm text-fg-muted">Couldn&apos;t load the agents. Reload to try again.</p>
        </PanelBody>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Agents, their models and 30-day cost</caption>
            <thead>
              <tr className="border-b border-border text-left text-caption text-fg-muted">
                <th scope="col" className="px-3.5 py-2 font-semibold">
                  Agent
                </th>
                <th scope="col" className="px-3.5 py-2 font-semibold">
                  Model
                </th>
                <th scope="col" className="px-3.5 py-2 font-semibold">
                  Fallback
                </th>
                <th scope="col" className="px-3.5 py-2 text-right font-semibold">
                  Temp
                </th>
                <th scope="col" className="px-3.5 py-2 text-right font-semibold">
                  30-day cost
                </th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.agent_key} className="border-b border-border last:border-b-0">
                  <th scope="row" className="px-3.5 py-2 text-left font-semibold">
                    {a.name}
                  </th>
                  <td className="px-3.5 py-2 font-mono text-xs">
                    {editable && a.editable ? (
                      <ModelSelector agent={a} disabled={saving !== null} onChoose={choose} />
                    ) : (
                      a.model
                    )}
                  </td>
                  <td className="px-3.5 py-2 font-mono text-xs text-fg-subtle">
                    {a.fallback_models.length ? a.fallback_models.join(", ") : <span className="font-sans">none</span>}
                  </td>
                  <td className="tabular px-3.5 py-2 text-right">{a.temperature.toFixed(1)}</td>
                  <td className="tabular px-3.5 py-2 text-right font-mono">
                    {showCost ? (
                      formatUsd(a.cost_30d_usd)
                    ) : (
                      <span className="text-fg-subtle">
                        <span aria-hidden="true">—</span>
                        <span className="sr-only">Not shown at your scope</span>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {error ? (
        <PanelBody>
          <p role="alert" className="text-caption font-medium text-danger">
            {error}
          </p>
        </PanelBody>
      ) : null}
      <PanelFoot>
        {editable ? "Only models with a known price can be chosen, so the cost caps stay meaningful. The next call uses the new model. " : ""}
        Every call routes through LiteLLM, so the provider is this string and nothing else.
        {editable ? "" : " Editing models arrives with agent definitions next sprint; until then this is the configuration in code."}
      </PanelFoot>
    </Panel>
  );
}

function CostRows({ title, rows }: { title: string; rows: CostRow[] }) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <h3 id={id} className="mb-1 text-caption font-semibold text-fg-muted">
        {title}
      </h3>
      {rows.length === 0 ? (
        <p className="text-caption text-fg-subtle">Nothing spent.</p>
      ) : (
        <ul className="grid">
          {byCost(rows).map((r) => (
            <li key={r.key} className="flex items-baseline gap-2 border-b border-dotted border-border-strong py-1 text-sm last:border-b-0">
              <span className="min-w-0 flex-1 truncate">{r.label}</span>
              <span className="tabular text-xs text-fg-subtle">
                {r.calls} {r.calls === 1 ? "call" : "calls"}
              </span>
              <span className="tabular w-16 text-right font-mono text-xs">{formatUsd(r.cost_usd)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CostPanel({ costs, tz, scoped }: { costs: CostReport | null; tz: string; scoped: boolean }) {
  const id = useId();
  return (
    <Panel aria-labelledby={`${id}-cost`}>
      <PanelHead>
        <PanelTitle id={`${id}-cost`}>Cost</PanelTitle>
      </PanelHead>
      {scoped ? (
        <PanelBody>
          <p className="text-sm text-fg-muted">{SCOPED_TEXT}</p>
        </PanelBody>
      ) : costs === null ? (
        <PanelBody>
          <p className="text-sm text-fg-muted">Couldn&apos;t load costs. Reload to try again.</p>
        </PanelBody>
      ) : (
        <PanelBody className="grid gap-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-sm">Last 30 days</span>
            <span className="font-mono text-md">{formatUsd(costs.total_usd)}</span>
          </div>
          <p className="text-caption text-fg-subtle">
            <Time value={costs.since} tz={tz} format="date" showZone={false} className="font-sans text-caption" /> to{" "}
            <Time value={costs.until} tz={tz} format="date" showZone={false} className="font-sans text-caption" />. Sandbox runs cost $0, and
            are counted as such.
          </p>
          <CostRows title="By agent" rows={costs.by_agent} />
          <CostRows title="By model" rows={costs.by_model} />
          <CostRows title="By workflow" rows={costs.by_workflow} />
          <CostRows title="By account" rows={costs.by_account} />
        </PanelBody>
      )}
    </Panel>
  );
}

function GuardrailsPanel() {
  const id = useId();
  return (
    <Panel aria-labelledby={`${id}-guardrails`}>
      <PanelHead>
        <PanelTitle id={`${id}-guardrails`}>Guardrails</PanelTitle>
      </PanelHead>
      <PanelBody className="grid gap-2">
        <dl className="grid gap-2">
          {GUARDRAIL_THRESHOLDS.map((g) => (
            <div key={g.label} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-2 gap-y-0.5">
              <dt className="text-sm">{g.label}</dt>
              <dd className="text-right font-mono text-xs">{g.value}</dd>
              {g.note ? <dd className="col-span-2 text-caption text-fg-subtle">{g.note}</dd> : null}
            </div>
          ))}
        </dl>
        <p className="text-caption text-fg-subtle">{GUARDRAILS_LATER.join(", ")} arrive later.</p>
        <div className="h-px bg-border" />
        <p className="text-caption text-fg-subtle">Guardrails run outside the agents, so an agent cannot influence its own gate.</p>
      </PanelBody>
    </Panel>
  );
}
