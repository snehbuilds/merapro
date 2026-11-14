import { getPending } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ConfirmAction } from "@/components/mcp/confirm-action";
import { Topbar } from "@/components/shell/topbar";
import { getSession, serverClient } from "@/lib/api/server";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: "Confirm an action" };

/* A high-risk call from an MCP client, confirmed here with a second factor (P2-M8.4). */
export default async function ConfirmPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = await params;
  const here = `/w/${slug}/confirm/${id}`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const r = await getPending({ client: await serverClient(), path: { workspace_id: workspace.id, pending_id: id } });
  if (!r.data) notFound();
  return (
    <>
      <Topbar title="Confirm an action" sub="Asked for by an MCP client" />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <ConfirmAction workspace={{ id: workspace.id, timezone: workspace.timezone }} pending={r.data} />
      </main>
    </>
  );
}
