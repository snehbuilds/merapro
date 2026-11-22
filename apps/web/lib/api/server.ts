import {
  agentCosts,
  contentStats,
  createMeraClient,
  getAgentRun,
  getContentItem,
  getPostSet as getPostSetOp,
  listAgentRuns,
  listAgents,
  listApprovalRequests,
  listAutomation,
  listPostSets,
  listVoices,
  getMemberScopes,
  getWorkspace,
  listAccounts,
  listApiKeys,
  listGroups,
  listInvitations,
  listMembers,
  listNotifications,
  listPillars,
  listSources,
  listHashtags,
  listTemplates,
  listWorkspaces,
  me,
  trending,
  type AccountOut,
  type ApiKeyOut,
  type GroupOut,
  type HashtagOut,
  type InvitationOut,
  type MeOut,
  type MeraClient,
  type NotificationOut,
  type ScopesOut,
  type TemplateOut,
  type WorkspaceOut,
} from "@mera/sdk";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";
import { apiBaseUrl } from "@/lib/session/constants";
import { readSessionTokens } from "@/lib/session/cookies";
import { loginHref } from "@/lib/session/next-path";
import { toApiError, type ApiError } from "./errors";
import type {
  Agent,
  AgentRun,
  ApprovalRequest,
  Automation,
  ContentItem,
  ContentStats,
  CostReport,
  Pillar,
  PostSet,
  Source,
  Voice,
  WorkspaceMember,
} from "./models";

/*
 * Server components call the API directly (no BFF hop) with the access
 * cookie. They cannot write cookies, so refresh happens earlier, in proxy.ts
 * (lib/session/gate.ts). Server-only: imports next/headers.
 */

export class ApiRequestError extends Error {
  constructor(readonly apiError: ApiError) {
    super(`${apiError.status} ${apiError.code}: ${apiError.message}`);
    this.name = "ApiRequestError";
  }
}

export const serverClient = cache(async (): Promise<MeraClient> => {
  const { accessToken } = readSessionTokens(await cookies());
  return createMeraClient({ baseUrl: apiBaseUrl(), accessToken });
});

interface SdkResult<T> {
  data?: T;
  error?: unknown;
  response?: Response;
}

/**
 * Unwrap an SDK result. 401 → redirect to login (with `next`); anything else
 * non-2xx throws ApiRequestError for the nearest error boundary.
 */
export async function unwrap<T>(call: Promise<SdkResult<T>>, next?: string): Promise<T> {
  let result: SdkResult<T>;
  try {
    result = await call;
  } catch (thrown) {
    throw new ApiRequestError(toApiError(thrown, 0));
  }
  const status = result.response?.status ?? 0;
  if (result.data !== undefined && result.response?.ok) return result.data;
  if (status === 401) redirect(loginHref(next));
  throw new ApiRequestError(toApiError(result.error, status));
}

export interface Session {
  user: MeOut;
  workspaces: WorkspaceOut[];
}

/** The signed-in user and their workspaces, once per request. */
const loadSession = cache(async () => {
  const client = await serverClient();
  return [me({ client }), listWorkspaces({ client })] as const;
});

export async function getSession(next?: string): Promise<Session> {
  const [userCall, workspaceCall] = await loadSession();
  const [user, page] = await Promise.all([unwrap(userCall, next), unwrap(workspaceCall, next)]);
  return { user, workspaces: page.data };
}

export async function getMembers(workspaceId: string, next?: string): Promise<WorkspaceMember[]> {
  const client = await serverClient();
  const page = await unwrap(listMembers({ client, path: { workspace_id: workspaceId } }), next);
  return page.data;
}

export async function getInvitations(workspaceId: string, next?: string): Promise<InvitationOut[]> {
  const client = await serverClient();
  const page = await unwrap(listInvitations({ client, path: { workspace_id: workspaceId } }), next);
  return page.data;
}

export async function getApiKeys(workspaceId: string, next?: string): Promise<ApiKeyOut[]> {
  const client = await serverClient();
  const page = await unwrap(listApiKeys({ client, path: { workspace_id: workspaceId } }), next);
  return page.data;
}

export async function getWorkspaceDetail(workspaceId: string, next?: string): Promise<WorkspaceOut> {
  const client = await serverClient();
  return unwrap(getWorkspace({ client, path: { workspace_id: workspaceId } }), next);
}

/** Every social account in the workspace (the caller's scope applies server-side). Once per request. */
const loadAccounts = cache(async (workspaceId: string) => {
  const client = await serverClient();
  return { call: listAccounts({ client, path: { workspace_id: workspaceId } }) };
});

export async function getAccounts(workspaceId: string, next?: string): Promise<AccountOut[]> {
  const { call } = await loadAccounts(workspaceId);
  return (await unwrap(call, next)).data;
}

export const getGroups = cache(async (workspaceId: string, next?: string): Promise<GroupOut[]> => {
  const client = await serverClient();
  const page = await unwrap(listGroups({ client, path: { workspace_id: workspaceId } }), next);
  return page.data;
});

/** Every source the caller can see (the API applies member scope). */
export async function getSources(workspaceId: string, next?: string): Promise<Source[]> {
  const client = await serverClient();
  const list = await unwrap(listSources({ client, path: { workspace_id: workspaceId } }), next);
  return list.data;
}

/** Trending items ranked for one group, or the workspace-wide maximum when groupId is null. */
export async function getTrending(
  workspaceId: string,
  opts: { groupId: string | null; hours: number; minRelevance: number; limit?: number },
  next?: string,
): Promise<ContentItem[]> {
  const client = await serverClient();
  const list = await unwrap(
    trending({
      client,
      path: { workspace_id: workspaceId },
      query: { group_id: opts.groupId, hours: opts.hours, min_relevance: opts.minRelevance, limit: opts.limit },
    }),
    next,
  );
  return list.data;
}

export async function getContentStats(
  workspaceId: string,
  opts: { groupId: string | null; days?: number },
  next?: string,
): Promise<ContentStats> {
  const client = await serverClient();
  return unwrap(
    contentStats({ client, path: { workspace_id: workspaceId }, query: { group_id: opts.groupId, days: opts.days ?? 7 } }),
    next,
  );
}

/** A group's pillars plus the workspace-wide ones; every visible pillar when groupId is null. */
export async function getPillars(workspaceId: string, groupId: string | null, next?: string): Promise<Pillar[]> {
  const client = await serverClient();
  const list = await unwrap(listPillars({ client, path: { workspace_id: workspaceId }, query: { group_id: groupId } }), next);
  return list.data;
}

export async function getNotifications(
  workspaceId: string,
  opts: { unreadOnly?: boolean } = {},
  next?: string,
): Promise<NotificationOut[]> {
  const client = await serverClient();
  const page = await unwrap(
    listNotifications({ client, path: { workspace_id: workspaceId }, query: { unread_only: opts.unreadOnly } }),
    next,
  );
  return page.data;
}

export async function getScopes(workspaceId: string, userId: string, next?: string): Promise<ScopesOut> {
  const client = await serverClient();
  return unwrap(getMemberScopes({ client, path: { workspace_id: workspaceId, user_id: userId } }), next);
}

/**
 * For secondary data (rail counts, dashboard notices): a failure renders the
 * page without it rather than taking the page down. Sign-out still redirects.
 */
export async function optional<T>(load: Promise<T>): Promise<T | undefined> {
  try {
    return await load;
  } catch (thrown) {
    if (thrown instanceof ApiRequestError) return undefined;
    throw thrown;
  }
}

/* ---- M5: post sets, brand voices, agents ---- */

/** null for a 404 (or a malformed id): the page says so instead of erroring. */
async function orNull<T>(load: Promise<T>): Promise<T | null> {
  try {
    return await load;
  } catch (thrown) {
    if (thrown instanceof ApiRequestError && (thrown.apiError.status === 404 || thrown.apiError.status === 422)) return null;
    throw thrown;
  }
}

/** A post set, or null when it doesn't exist or the caller can't see it. */
export async function getPostSet(workspaceId: string, setId: string, next?: string): Promise<PostSet | null> {
  const client = await serverClient();
  return orNull(unwrap(getPostSetOp({ client, path: { workspace_id: workspaceId, set_id: setId } }), next));
}

/** The most recent post sets (archived ones left out). */
export async function getRecentPostSets(workspaceId: string, limit: number, next?: string): Promise<PostSet[]> {
  const client = await serverClient();
  const list = await unwrap(listPostSets({ client, path: { workspace_id: workspaceId }, query: { limit } }), next);
  return list.data;
}

/** One content item, or null when it doesn't exist. */
export async function getContentItemById(workspaceId: string, itemId: string, next?: string): Promise<ContentItem | null> {
  const client = await serverClient();
  return orNull(unwrap(getContentItem({ client, path: { workspace_id: workspaceId, item_id: itemId } }), next));
}

/** Every brand voice the caller can see, at every scope. */
export async function getVoices(workspaceId: string, next?: string): Promise<Voice[]> {
  const client = await serverClient();
  const list = await unwrap(listVoices({ client, path: { workspace_id: workspaceId } }), next);
  return list.data;
}

/** Approval requests, newest first. state: "open" (default), one state, or "" for all. */
export async function getApprovalRequests(
  workspaceId: string,
  opts: { state?: string; limit?: number } = {},
  next?: string,
): Promise<ApprovalRequest[]> {
  const client = await serverClient();
  const list = await unwrap(
    listApprovalRequests({ client, path: { workspace_id: workspaceId }, query: { state: opts.state ?? "open", limit: opts.limit } }),
    next,
  );
  return list.data;
}

export async function getAutomation(workspaceId: string, next?: string): Promise<Automation[]> {
  const client = await serverClient();
  const list = await unwrap(listAutomation({ client, path: { workspace_id: workspaceId } }), next);
  return list.data;
}

export async function getAgents(workspaceId: string, next?: string): Promise<Agent[]> {
  const client = await serverClient();
  const list = await unwrap(listAgents({ client, path: { workspace_id: workspaceId } }), next);
  return list.data;
}

export async function getAgentRuns(workspaceId: string, opts: { limit?: number } = {}, next?: string): Promise<AgentRun[]> {
  const client = await serverClient();
  const list = await unwrap(listAgentRuns({ client, path: { workspace_id: workspaceId }, query: { limit: opts.limit } }), next);
  return list.data;
}

/** One run with its steps, or null when it doesn't exist. */
export async function getAgentRunById(workspaceId: string, runId: string, next?: string): Promise<AgentRun | null> {
  const client = await serverClient();
  return orNull(unwrap(getAgentRun({ client, path: { workspace_id: workspaceId, run_id: runId } }), next));
}

/** Cost rollups; the API's default window is the last 30 days. */
export async function getAgentCosts(workspaceId: string, next?: string): Promise<CostReport> {
  const client = await serverClient();
  return unwrap(agentCosts({ client, path: { workspace_id: workspaceId } }), next);
}

/**
 * Like `optional`, but keeps the status: a 403 (e.g. workspace-wide data a
 * group-scoped member can't see) reads differently from a failure.
 */
export async function attempt<T>(load: Promise<T>): Promise<{ ok: true; data: T } | { ok: false; status: number }> {
  try {
    return { ok: true, data: await load };
  } catch (thrown) {
    if (thrown instanceof ApiRequestError) return { ok: false, status: thrown.apiError.status };
    throw thrown;
  }
}

/** Post templates (P2-M1.4). */
export async function getTemplates(workspaceId: string, next?: string): Promise<TemplateOut[]> {
  const page = await unwrap(listTemplates({ client: await serverClient(), path: { workspace_id: workspaceId } }), next);
  return page.data;
}

/** The hashtag registry (P2-M1.3). */
export async function getHashtags(workspaceId: string, next?: string): Promise<HashtagOut[]> {
  const page = await unwrap(listHashtags({ client: await serverClient(), path: { workspace_id: workspaceId } }), next);
  return page.data;
}
