import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AgentsScreen } from "@/components/agents/agents-screen";
import { attempt, getAgentCosts, getAgentRunById, getAgentRuns, getAgents, getSession, optional } from "@/lib/api/server";
import { RUNS_LIMIT } from "@/lib/agents";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isAdminRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.agents.title };

/*
 * Agents (agents.html). Live since Sprint 6 (M5), read-only: the run viewer
 * (?run=<id>, else the latest run), model routing per agent, costs and the
 * guardrail thresholds. Retry / abort and editing models arrive with agent
 * definitions next sprint.
 */
export default async function AgentsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const query = await searchParams;
  const here = `/w/${slug}/agents`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  const requested = typeof query.run === "string" && query.run ? query.run : undefined;
  // Each panel degrades on its own if its call fails. Runs and costs span every
  // account, so the API refuses them (403) to group-scoped members.
  const [agents, runsResult, costsResult] = await Promise.all([
    optional(getAgents(workspace.id, here)),
    attempt(getAgentRuns(workspace.id, { limit: RUNS_LIMIT }, here)),
    attempt(getAgentCosts(workspace.id, here)),
  ]);
  const runs = runsResult.ok ? runsResult.data : null;
  const costs = costsResult.ok ? costsResult.data : null;
  const scoped = (!runsResult.ok && runsResult.status === 403) || (!costsResult.ok && costsResult.status === 403);
  const firstId = scoped ? undefined : (requested ?? runs?.[0]?.id);
  const initialRun = firstId ? ((await optional(getAgentRunById(workspace.id, firstId, here))) ?? null) : null;

  return (
    <AgentsScreen
      workspace={{
        id: workspace.id,
        slug: workspace.slug,
        name: workspace.name,
        timezone: workspace.timezone,
        archived: workspace.archived,
      }}
      agents={agents ?? null}
      runs={runs ?? null}
      initialRun={initialRun}
      costs={costs}
      scoped={scoped}
      canManage={isAdminRole(workspace.role)}
    />
  );
}
