import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SandboxScreen } from "@/components/sandbox/sandbox-screen";
import { getSession } from "@/lib/api/server";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: "Sandbox inspector" };

/* The sandbox inspector (M9.17): the simulated phone and every simulated platform call. */
export default async function SandboxPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const session = await getSession(`/w/${slug}/sandbox`);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  return <SandboxScreen workspace={{ id: workspace.id, slug: workspace.slug, timezone: workspace.timezone }} />;
}
