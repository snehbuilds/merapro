import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Icon } from "@/components/icons";
import { Topbar } from "@/components/shell/topbar";
import { WorkspaceAccessNotice } from "@/components/security/workspace-access-notice";
import { InvitationsPanel } from "@/components/team/invitations-panel";
import { MembersTable, type MemberRow } from "@/components/team/members-table";
import { ScopeEditor } from "@/components/team/scope-editor";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { getAccounts, getGroups, getInvitations, getMembers, getScopes, getSession, getWorkspaceDetail, optional } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { INVITE_FORM_ID, findBySlug, isAdminRole, scopeSummary } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.team.title };

/*
 * Team & Roles (team.html). Live: members (role change and removal for
 * Admin+), invitations, and each member's scope, read-only (the scope editor
 * is Phase 2, docs/11 §5). The permissions matrix is still to come.
 */
export default async function TeamPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/team`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  const assignedAdmin = isAdminRole(workspace.role);
  const membersCall = getMembers(workspace.id, here);
  const accessCall = assignedAdmin
    ? getWorkspaceDetail(workspace.id, here).then((detail) => isAdminRole(detail.role))
    : Promise.resolve(false);
  const [members, canManage] = await Promise.all([membersCall, accessCall]);
  // Scopes are readable by Admin+ for anyone, and by each member for themself.
  const readable = members.filter((m) => m.scope_restricted && (canManage || m.user_id === session.user.id));
  const [groups, accounts, scopes, invitations] =
    readable.length > 0 || canManage
      ? await Promise.all([
          optional(getGroups(workspace.id, here)),
          optional(getAccounts(workspace.id, here)),
          Promise.all(readable.map((m) => optional(getScopes(workspace.id, m.user_id, here)))),
          canManage ? getInvitations(workspace.id, here) : Promise.resolve([]),
        ])
      : [undefined, undefined, [], []];
  const scopeLabels = new Map<string, string>();
  readable.forEach((m, i) => {
    const s = scopes[i];
    if (s) scopeLabels.set(m.user_id, scopeSummary(s, groups ?? [], accounts ?? []));
  });

  const rows: MemberRow[] = members.map((m) => ({
    ...m,
    tz: workspace.timezone,
    isYou: m.user_id === session.user.id,
    workspaceId: workspace.id,
    canManage,
    readOnly: workspace.archived,
    scopeLabel: scopeLabels.get(m.user_id),
  }));
  const screen = SCREENS.team;
  const action = canManage && !workspace.archived ? { label: "Invite", href: `#${INVITE_FORM_ID}` } : undefined;

  return (
    <>
      <Topbar title={screen.title} sub={`${members.length} ${members.length === 1 ? "member" : "members"}`} action={action} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        {assignedAdmin && !canManage ? <WorkspaceAccessNotice slug={slug} /> : null}
        <Panel>
          <PanelHead title="Members" actions={<span className="text-caption text-fg-subtle">{workspace.name} workspace</span>} />
          <PanelBody flush>
            <MembersTable members={rows} workspaceName={workspace.name} />
          </PanelBody>
          <PanelFoot>
            Scope narrows a role; it never widens one. A scoped member cannot see, or act on, anything outside their
            scope — through the web UI, the API, MCP, or the bot.
          </PanelFoot>
        </Panel>

        {canManage && !workspace.archived ? (
          <ScopeEditor
            workspaceId={workspace.id}
            members={members.map((m) => ({ id: m.user_id, name: m.display_name, role: m.role }))}
            groups={(groups ?? []).map((g) => ({ id: g.id, name: g.name }))}
            accounts={(accounts ?? []).map((a) => ({ id: a.id, nickname: a.nickname }))}
          />
        ) : null}

        {canManage ? (
          <InvitationsPanel
            workspaceId={workspace.id}
            invitations={invitations}
            tz={workspace.timezone}
            archived={workspace.archived}
          />
        ) : null}

        <Panel>
          <PanelHead title="What each role can do" />
          <PanelBody>
            <div className="grid place-items-center gap-2 px-6 py-11 text-center">
              <Icon name="team" size="xl" className="text-fg-subtle" />
              <p className="max-w-[46ch] text-sm text-fg-muted">Not built yet — see docs/12 §4</p>
            </div>
          </PanelBody>
        </Panel>
      </main>
    </>
  );
}
