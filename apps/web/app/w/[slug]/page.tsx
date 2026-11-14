import { redirect } from "next/navigation";
import { screenHref } from "@/lib/nav";

export default async function WorkspaceIndex({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  redirect(screenHref(slug, "dashboard"));
}
