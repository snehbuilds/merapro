import type { Metadata } from "next";
import Link from "next/link";
import { CreateWorkspaceForm } from "@/components/auth/create-workspace-form";
import { Panel, PanelBody, PanelFoot } from "@/components/ui/panel";
import { getSession } from "@/lib/api/server";
import { screenHref } from "@/lib/nav";

export const metadata: Metadata = { title: "Create a workspace" };

/** Onboarding: where a signed-in user with no workspace lands (app/page.tsx). */
export default async function WelcomePage() {
  const { user, workspaces } = await getSession("/welcome");
  const first = workspaces[0];
  return (
    <Panel aria-labelledby="welcome-title">
      <PanelBody className="grid gap-5 p-6 max-narrow:p-4">
        <div className="grid gap-1">
          <h1 id="welcome-title" className="text-lg">
            {first ? "Create a workspace" : "Create your first workspace"}
          </h1>
          <p className="text-sm text-fg-muted">
            {first
              ? "A workspace holds its own accounts, groups, team and approval rules."
              : `Welcome, ${user.display_name}. A workspace holds your accounts, groups, team and approval rules.`}
          </p>
        </div>
        <CreateWorkspaceForm />
      </PanelBody>
      {first ? (
        <PanelFoot>
          Or go back to <Link href={screenHref(first.slug, "dashboard")}>{first.name}</Link>
        </PanelFoot>
      ) : null}
    </Panel>
  );
}
