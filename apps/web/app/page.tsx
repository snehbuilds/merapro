import { redirect } from "next/navigation";
import { getSession } from "@/lib/api/server";
import { screenHref } from "@/lib/nav";

/** Signed in: the first workspace's dashboard, or onboarding when there are none. */
export default async function Home() {
  const { workspaces } = await getSession("/");
  const first = workspaces[0];
  redirect(first ? screenHref(first.slug, "dashboard") : "/welcome");
}
